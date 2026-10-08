"""AST-driven Qt6 interface integrity test for every tab mixin.

Walks the AST of every method in every tab mixin (auto-discovered from
``tabs/__init__.py``) and diffs every ``self.<var>.<method>`` / local
``<var>.<method>`` call against the actual PyQt6 class hierarchy.

Catches the regression class ``btn.settxt()`` (instead of ``setText()``)
before it ever reaches a running session -- and keeps the audit in sync
with the canonical mixin registry so adding or removing a tab in
``tabs/__init__.py`` is the only place the test list needs to be touched.
"""

from __future__ import annotations
import ast
import pathlib
import sys as _sys
import unittest


_sys.path.insert(0, r"C:\\KokertechAI")

# FIX: PROJECT_ROOT must point to the actual project root (parent of tests/),
# not the tests/ directory itself. The tabs/ package lives at the project root,
# not inside tests/. Using tests/ as PROJECT_ROOT caused _discover_tabs_from_init
# to look for tests/tabs/__init__.py (which doesn't exist and would create a
# namespace collision with the real tabs/ package if created).
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
TABS_DIR = PROJECT_ROOT / "tabs"

QT_CLASSES: dict[str, type] = {}


def _index_qt_classes() -> None:
    """Enumerate every real ``Q*`` class in PyQt6.QtCore / QtGui / QtWidgets.

    Real classes are filtered with ``isinstance(obj, type)`` so constants,
    enums, and signal objects don't pollute the index.
    """
    from PyQt6 import QtCore, QtGui, QtWidgets
    try:
        from PyQt6 import QtSvg
        _svg_modules = (QtSvg,)
    except ImportError:
        _svg_modules = ()
    try:
        from PyQt6 import QtWebEngineWidgets
        _web_modules = (QtWebEngineWidgets,)
    except ImportError:
        _web_modules = ()
        # When QtWebEngine DLLs aren't available (e.g. testing environment
        # without the platform-dependent Qt6WebEngine*.dll on PATH),
        # register known QWebEngine classes as stubs so the AST class-name
        # check doesn't flag usage behind _web_engine_available guards.
        # The method-validation test will still skip unknown stubs, but
        # the class-name test just needs the key to exist.
        _QWEBENGINE_STUB = type(
            "QWebEngineView",
            (),
            {
                "setUrl": lambda s, u: None,
                "back": lambda s: None,
                "forward": lambda s: None,
                "reload": lambda s: None,
                "setStyleSheet": lambda s, ss: None,
                "page": lambda s: None,
                "loadProgress": None,
                "urlChanged": None,
                "loadFinished": None,
            },
        )
        QT_CLASSES["QWebEngineView"] = _QWEBENGINE_STUB
    for module in (QtCore, QtGui, QtWidgets) + _svg_modules + _web_modules:
        for name in dir(module):
            if not name.startswith("Q"):
                continue
            obj = getattr(module, name)
            if isinstance(obj, type):
                QT_CLASSES[name] = obj


_index_qt_classes()


def _extract_qt_class_name(call_node: ast.Call) -> str | None:
    """Return the Qt class name for a constructor call (``QPushButton(...)`` or
    ``widgets.QPushButton(...)``); ``None`` if the call isn't a ``Q*`` ctor.
    """
    f = call_node.func
    if isinstance(f, ast.Name) and f.id.startswith("Q"):
        return f.id
    if isinstance(f, ast.Attribute) and f.attr.startswith("Q"):
        return f.attr
    return None


def _discover_tabs_from_init() -> list[tuple[str, str]]:
    """Auto-derive ``(filename, MixinClassName)`` pairs from ``tabs/__init__.py``.

    Scans every ``from .<file> import <Name1>[, <Name2>, ...]`` line and
    keeps only PascalCase names that match the Mixin convention (contain
    ``"Mixin"`` or end with ``"Tab"``). Helper symbols imported alongside
    Mixins (e.g. ``_ipc_bus``) are dropped -- prevents unrelated
    housekeeping imports from creating fake audit pairs.

    AST quirk to remember: ``from .workspace_tab import X`` parses to
    ``ImportFrom(module="workspace_tab", level=1, names=[X])``. The
    leading dot from the source syntax is captured by ``level``, NOT by
    ``module`` -- ``module`` has no dot.

    Raises ``RuntimeError`` if ``tabs/__init__.py`` is missing or yields
    no canonical Mixin-class pairs (silent empty discovery would let the
    four tests pass trivially and turn the regression trip-wire into a
    paper tiger).
    """
    init_path = TABS_DIR / "__init__.py"
    if not init_path.exists():
        raise RuntimeError(
            f"_discover_tabs_from_init: tabs/__init__.py not found at {init_path}. "
            f"Cannot auto-derive tab mixins; tests cannot proceed."
        )
    src = init_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    pairs: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.level == 1):
            module_file = node.module + ".py"
            for alias in node.names:
                name = alias.name
                # Q5: filter to PascalCase Mixin-class names. Keeps
                # ``WorkspaceTabMixin``-shape names, drops helpers like
                # ``_ipc_bus`` or constants imported alongside Mixins.
                if (name
                        and name[0].isupper()
                        and ("Mixin" in name or name.endswith("Tab"))):
                    pairs.append((module_file, name))
    if not pairs:
        raise RuntimeError(
            "_discover_tabs_from_init: no PascalCase Mixin-class imports "
            "found in tabs/__init__.py. Empty test list would let the suite "
            "pass trivially; refusing to silently pass."
        )
    return pairs


def _walk_attr_chain(receiver: ast.AST) -> tuple[str, tuple[str, ...]] | None:
    """Descend an ``Attribute`` chain to find the root ``Name`` node.

    Returns ``(root_id, intermediates)`` where ``root_id`` is the deepest
    ``Name.id`` and ``intermediates`` lists every ``Attribute.attr`` between
    root and outermost. Returns ``None`` if the chain doesn't end at a Name
    (e.g. it terminates at a Call like ``foo().bar``).

    Examples::

        self.btn                       -> ('self',           ('btn',))
        self.btn.clicked               -> ('self',           ('btn', 'clicked'))
        self.btn.clicked.connect       -> ('self',           ('btn', 'clicked', 'connect'))
        foo().bar                      -> None              (root is a Call)
    """
    attrs: list[str] = []
    while isinstance(receiver, ast.Attribute):
        attrs.insert(0, receiver.attr)
        receiver = receiver.value
    if isinstance(receiver, ast.Name):
        return (receiver.id, tuple(attrs))
    return None


# Methods that may legitimately appear as the OUTERMOST call in a CHAINED
# attribute access -- ``self.<var>.<inter>...<method>``. Signal-slot wiring
# (``connect`` / ``disconnect`` / ``emit``) is runtime-dispatched on bound
# signal objects; static ``hasattr`` against the Q class can't see them.
# This allowlist prevents false positives on the dominant Qt UX pattern
# ``self.btn.clicked.connect(handler)`` while still flagging typos like
# ``self.btn.clicked.conenct(handler)``.
QT_CHAIN_METHOD_ALLOWLIST: frozenset[str] = frozenset({
    # Signal-slot primary contract (PyQt6 dispatches on bound signal objects;
    # static hasattr against the Q class can't see them):
    "connect",
    "disconnect",
    "emit",
    # Common PyQt6 signal names that widgets expose (each widget's signal
    # descriptors appear as runtime attributes; the allowlist is the static-
    # analysis bridge between AST chain detection and runtime dispatch):
    "toggled",
    "pressed",
    "released",
    "clicked",
    "stateChanged",
    "valueChanged",
    "textChanged",
    "editingFinished",
    "returnPressed",
    "selectionChanged",
    "currentRowChanged",
    "currentTextChanged",
    "currentIndexChanged",
    "itemChanged",
    "cellChanged",
    "cellClicked",
    "cellDoubleClicked",
    "triggered",
    "hovered",
    "windowTitleChanged",
    "linkActivated",
    "linkHovered",
    "rangeChanged",
    "sliderMoved",
    "sliderPressed",
    "sliderReleased",
    # Common property / state accessors chained from signals or getters:
    "isVisible",
    "isEnabled",
    "isChecked",
    "isHidden",
    "isModal",
    "isActiveWindow",
    "isMinimized",
    "isMaximized",
    "isFullScreen",
    "text",
    "value",
    "checked",
    "currentText",
    "currentIndex",
    "currentRow",
    "currentData",
    "count",
    "length",
    "size",
    "toPlainText",
    "toHtml",
    "toMarkdown",
})


def _walk_tab_all_methods(tab_path: pathlib.Path, mixin_name: str):
    """Walk every ``FunctionDef`` in the mixin class body and aggregate
    QWidget assignments + method calls.

    Two-pass approach (class-scope, NOT method-scope):

    1. Pass 1 walks every method and records ``Q*()`` /
       ``self.<name> = Q*()`` assignments into a single class-wide map.
    2. Pass 2 walks every method and records ``self.<var>.<m>()`` /
       local ``<var>.<m>()`` calls against the accumulated map.

    Skips ``__init__`` / ``__eq__`` / other dunder methods. Catches the
    helper-method regression class (e.g., ``btn.settxt()`` inside
    ``_create_sys_config_group``) -- not just top-level ``create_*_tab``.

    Returns ``(method_names, qt_var_to_class, method_calls)`` where
    ``method_calls`` items are
    ``(var, method, defining_method, lineno, chain_depth, qt_class_name)``.
    The 6th element is the Q class resolved at walk time via the calling
    method's per-method map (with class-wide fallback), so the test
    class method doesn't need to do its own lookup.

    KNOWN LIMITATION: within-method reassignments (e.g., `label =
    QGraphicsTextItem(...)` then `label = QLabel(...)` then
    `label.setPos()` in the same method) are not tracked -- the
    per-method map records the LAST assignment, so calls on an earlier
    type will be checked against the later type. Fixing this requires
    dataflow analysis (SSA-style tracking), which is out of scope.
    """
    src = tab_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    mixin_class = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.ClassDef) and n.name == mixin_name),
        None,
    )
    if mixin_class is None:
        return [], {}, []

    method_names: list[str] = []
    qt_var_to_class: dict[str, tuple[str, str]] = {}
    # Per-method variable maps: for each method, the variables defined in
    # that method and their Q class. Used in Pass 2 to resolve the correct
    # class for a call when the same variable name is reassigned with a
    # different Q type in different methods (e.g., `label` is
    # `QGraphicsTextItem` in `draw_knowledge_graph` but `QLabel` elsewhere).
    # Without per-method resolution, the class-scope `qt_var_to_class` map
    # would return the LAST assignment, causing false positives when a
    # call's actual receiver type differs from the last-assigned type.
    method_var_maps: dict[str, dict[str, tuple[str, str]]] = {}

    # Pass 1: collect widget assignments from EVERY method in the class.
    for member in mixin_class.body:
        if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if member.name.startswith("__") and member.name.endswith("__"):
            continue
        method_names.append(member.name)
        method_var_maps[member.name] = {}
        # Also record ``for <var> in ...:`` targets in the per-method map
        # with a ``"__CUSTOM__"`` sentinel. These are often multi-type
        # iteration variables (e.g. ``for item in scene.items():`` where
        # ``item`` is also a ``QListWidgetItem`` in another method) and
        # the sentinel prevents false-positive fallback to the class-wide
        # map when the loop variable type can't be statically determined.
        for for_node in ast.walk(member):
            if isinstance(for_node, ast.For) and isinstance(for_node.target, ast.Name):
                method_var_maps[member.name][for_node.target.id] = ("__CUSTOM__", member.name)
        for node in ast.walk(member):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            qt_class = _extract_qt_class_name(node.value)
            # Record ALL constructor assignments in the per-method map,
            # even non-Q* classes like TextLabelItem. This fixes false
            # positives from variable names reused across methods with
            # different types (e.g. ``label = QLabel(...)`` in one
            # method but ``label = TextLabelItem(...)`` in another).
            # Without this, the per-method fallback to the class-wide
            # map picks up the Q-type assignment from a different
            # method and flags method calls that are valid on the
            # non-Q* type.
            recorded_class = qt_class or "__CUSTOM__"
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if qt_class is not None:
                        qt_var_to_class[target.id] = (qt_class, member.name)
                    method_var_maps[member.name][target.id] = (recorded_class, member.name)
                elif (isinstance(target, ast.Attribute)
                      and isinstance(target.value, ast.Name)
                      and target.value.id == "self"):
                    if qt_class is not None:
                        qt_var_to_class[target.attr] = (qt_class, member.name)
                    method_var_maps[member.name][target.attr] = (recorded_class, member.name)

    # Pass 2: collect method calls against the per-method maps. Three patterns
    # are now covered:
    #
    #   1. SHALLOW self:    ``self.<var>.<method>()``
    #   2. SHALLOW local:   ``<local_var>.<method>()``
    #   3. CHAINED (sigslot): ``self.<var>.<inter>...<method>()``
    #
    # Each emitted tuple is
    # ``(var, method, member_name, lineno, chain_depth, qt_class_name)``
    # where ``chain_depth == 0`` means shallow (strict hasattr) and
    # ``chain_depth >= 1`` means chained (allowlist + lenient hasattr).
    # The 6th element is the Q class resolved via the CALLING method's
    # per-method map (with class-wide fallback), so the test class method
    # doesn't need to do its own lookup. This fixes the false-positive
    # class where a variable is reassigned with a different Q type in
    # different methods (e.g., `label` is `QGraphicsTextItem` in
    # `draw_knowledge_graph` but `QLabel` elsewhere) -- the class-scope
    # map would return the LAST assignment, but the per-method map
    # returns the assignment visible to the call's own method.
    method_calls: list[tuple[str, str, str, int, int, str]] = []
    for member in mixin_class.body:
        if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Per-method lookup: prefer the variable defined in this method,
        # fall back to the class-wide map (for vars defined in __init__
        # or other methods, accessed cross-method).
        method_local = method_var_maps.get(member.name, {})
        for node in ast.walk(member):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if not isinstance(f, ast.Attribute):
                continue
            method = f.attr
            receiver = f.value

            def _resolve(var_name: str) -> tuple[str, str] | None:
                """Return ``(qt_class, defining_method)`` for var_name,
                preferring the calling method's own definition."""
                return method_local.get(var_name) or qt_var_to_class.get(var_name)

            # Pattern 1: SHALLOW self -- ``self.<var>.<method>()``.
            if (isinstance(receiver, ast.Attribute)
                    and isinstance(receiver.value, ast.Name)
                    and receiver.value.id == "self"):
                entry = _resolve(receiver.attr)
                if entry is not None:
                    method_calls.append(
                        (receiver.attr, method, member.name,
                         node.lineno, 0, entry[0]))
                continue

            # Pattern 2: SHALLOW local -- ``<local_var>.<method>()``.
            if isinstance(receiver, ast.Name):
                entry = _resolve(receiver.id)
                if entry is not None:
                    method_calls.append(
                        (receiver.id, method, member.name,
                         node.lineno, 0, entry[0]))
                continue
            # Pattern 3: CHAINED signal-slot / chained attribute -- accepts
            # both ``self.<var>.<inter>...<method>`` and
            # ``<local_qwidget>.<inter>...<method>``. Descend the chain
            # until reaching a Name root, then resolve the WIDGET VAR:
            #   - self-rooted chains: widget var = ``intermediates[0]``
            #   - local-var-rooted chains: widget var = ``root_name``
            # Both forms are valid because ``qt_var_to_class`` keys are
            # uniformly var names whether assigned to local names
            # (``x = QPushButton()``) or self attrs (``self.x = QPushButton()``).
            chain = _walk_attr_chain(receiver)
            if chain is None:
                continue
            root_name, intermediates = chain
            # Guard: an empty `intermediates` tuple means the chain is just
            # `self.<method>()` (e.g. self.refresh()) with no Q-widget
            # receiver. Pattern 1/2 already filter these by checking the
            # outer attribute/name against qt_var_to_class, so reaching
            # Pattern 3 with an empty chain means the call is on `self` (or
            # another non-Q local) and we should skip it -- the outer
            # method has no Q-widget to validate against. Without this
            # guard, `intermediates[0]` raises IndexError.
            if not intermediates:
                continue
            chain_widget = (
                intermediates[0] if root_name == "self" else root_name)
            if intermediates:
                entry = _resolve(chain_widget)
                if entry is not None:
                    method_calls.append(
                        (chain_widget, method, member.name,
                         node.lineno, len(intermediates), entry[0]))
                # NOTE: deeper intermediates (e.g. ``clicked`` in
                # ``self.btn.clicked.connect``) are not strict-validated
                # -- PyQt6 signal/attribute dispatch is dynamic at runtime.
                # The allowlist check in the test handles the outermost
                # method name; intermediate attributes are accepted as-is.

    return method_names, qt_var_to_class, method_calls


class TestTabQtInterface(unittest.TestCase):
    """End-to-end AST-driven Qt6 interface check for every tab mixin.

    Discovered by ``pytest.ini`` ``testpaths = .`` so every CI invocation
    runs these four gates -- a regression on any tab (typo in a method
    name, missing ``Q``-prefix ctor, helper-method mishap) halts the
    suite at startup-time coverage.
    """

    TABS = _discover_tabs_from_init()

    def test_every_tab_mixin_file_exists_with_class(self):
        missing_files: list[str] = []
        missing_class: list[tuple[str, str, list[str]]] = []
        for tab_filename, mixin_name in self.TABS:
            tab_path = TABS_DIR / tab_filename
            if not tab_path.exists():
                missing_files.append(tab_filename)
                continue
            src = tab_path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            classes = [n.name for n in ast.walk(tree)
                       if isinstance(n, ast.ClassDef)]
            if mixin_name not in classes:
                missing_class.append((tab_filename, mixin_name, classes))
        self.assertEqual(
            missing_files, [],
            f"Tab files missing from disk: {missing_files}",
        )
        self.assertEqual(
            missing_class, [],
            "Tab files where the declared MixinClass is not defined:\n  "
            + "\n  ".join(
                f"{fn}: declared={m!r}  found={c!r}"
                for fn, m, c in missing_class
            ),
        )

    def test_every_tab_create_method_exists(self):
        no_create: list[str] = []
        for tab_filename, mixin_name in self.TABS:
            tab_path = TABS_DIR / tab_filename
            if not tab_path.exists():
                continue
            src = tab_path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            mixin_class = next(
                (n for n in ast.walk(tree)
                 if isinstance(n, ast.ClassDef) and n.name == mixin_name),
                None,
            )
            if mixin_class is None:
                continue
            has_create = any(
                isinstance(n, ast.FunctionDef)
                and n.name.startswith("create_") and n.name.endswith("_tab")
                for n in mixin_class.body
            )
            if not has_create:
                no_create.append(
                    f"{tab_filename}/{mixin_name}: no create_*_tab method"
                )
        self.assertEqual(
            no_create, [],
            "Tabs missing the create_*_tab method (auto-discovery convention):\n  "
            + "\n  ".join(no_create),
        )

    def test_qt_classes_used_in_all_methods_resolve(self):
        bad: list[str] = []
        for tab_filename, mixin_name in self.TABS:
            tab_path = TABS_DIR / tab_filename
            if not tab_path.exists():
                continue
            method_names, qt_var_to_class, _ = _walk_tab_all_methods(
                tab_path, mixin_name)
            if not method_names:
                continue
            invalid = sorted({
                qt for qt, _ in qt_var_to_class.values()
                if qt not in QT_CLASSES
            })
            if invalid:
                bad.append(
                    f"{tab_filename}/{mixin_name}: "
                    f"unrecognized Qt classes: {invalid}"
                )
        self.assertEqual(
            bad, [],
            "Tabs referencing Qt classes that don't exist in PyQt6 6.11.0:\n  "
            + "\n  ".join(bad),
        )

    def test_method_calls_on_qt_widgets_resolve(self):
        bad: list[str] = []
        for tab_filename, mixin_name in self.TABS:
            tab_path = TABS_DIR / tab_filename
            if not tab_path.exists():
                continue
            method_names, qt_var_to_class, method_calls = _walk_tab_all_methods(
                tab_path, mixin_name)
            if not method_calls:
                continue
            invalid_for_tab: list[str] = []
            for var, method, mname, lineno, chain_depth, qt_class_name in method_calls:
                if not qt_class_name:
                    continue
                klass = QT_CLASSES.get(qt_class_name)
                if klass is None:
                    continue
                klass_name = klass.__name__
                if chain_depth == 0:
                    # Strict: every shallow ``<var>.<method>`` MUST exist on
                    # the assigned Q class. Catches the regression class
                    # ``btn.settxt()`` (instead of ``setText()``).
                    if not hasattr(klass, method):
                        invalid_for_tab.append(
                            f"{mname}.L{lineno}: {var}.{method}() "
                            f"-- no such method on {klass_name}"
                        )
                    continue
                # Chained signal-slot / property access. Outermost method
                # MUST be in ``QT_CHAIN_METHOD_ALLOWLIST`` -- no implicit
                # hasattr escape hatch (signal dispatch is runtime and
                # hasattr on the Q class wouldn't reflect it anyway). This
                # explicit contract catches the typo class
                # ``self.btn.clicked.conenct(...)``.
                if method not in QT_CHAIN_METHOD_ALLOWLIST:
                    chain_label = (
                        f"{var}.<...{chain_depth} hop(s)...>.{method}"
                    )
                    invalid_for_tab.append(
                        f"{mname}.L{lineno}: {chain_label}() "
                        f"-- not in chain-allowlist "
                        f"(allowlist has {len(QT_CHAIN_METHOD_ALLOWLIST)} "
                        f"entries)"
                    )
            if invalid_for_tab:
                bad.append(
                    f"{tab_filename}/{mixin_name}: "
                    f"{len(invalid_for_tab)} invalid call(s): "
                    + "; ".join(invalid_for_tab[:5])
                )
        self.assertEqual(
            bad, [],
            "Tabs calling Qt methods that don't exist on the assigned "
            "widget class (or on chained signal-slot contracts):\n  "
            + "\n  ".join(bad),
        )
