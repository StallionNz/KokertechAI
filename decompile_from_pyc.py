#!/usr/bin/env python3
"""Decompile Python 3.12 .pyc files using marshal-loaded code objects."""

import sys
import marshal
import pathlib
import types
import re
try:
    import dis
except ImportError:
    dis = None  # dis module is stdlib, but guard for edge cases


# Project import lookup: maps symbol name -> (source_module, import_statement)
# Used to detect project imports from co_names and avoid self-imports.
# ── Bytecode-to-source helpers ──────────────────────────────────────
# Translates Python 3.12 bytecode instructions back into Python source.

# Binary operator names indexed by BINARY_OP oparg (Python 3.12+)
# Verified against Python 3.14.6 dis output.
_BINARY_OP_NAMES = {
    0: '+', 1: '&', 2: '//', 3: '<<', 4: '@',
    5: '*', 6: '%', 7: '|', 8: '**', 9: '>>',
    10: '-', 11: '/', 12: '^',
}

# For in-place BINARY_OP opargs (13-25), map to the base operator symbol,
# so augmented assignments like x += 2 decompile as `x = x + 2`.
_BINARY_OP_INPLACE_TO_BASE = {
    13: '+', 14: '&', 15: '//', 16: '<<', 17: '@',
    18: '*', 19: '%', 20: '|', 21: '**', 22: '>>',
    23: '-', 24: '/', 25: '^',
}

# BINARY_OP oparg for subscript access (BINARY_SUBSCR, Python 3.14+)
_BINARY_OP_SUBSCR = 26

# Instructions that act as statement boundaries (stop expression reads)
_STATEMENT_BOUNDARY_OPS = frozenset({
    'RETURN_VALUE', 'POP_TOP',
    'STORE_FAST', 'STORE_NAME', 'STORE_GLOBAL', 'STORE_ATTR', 'STORE_SUBSCR',
    'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE',
    'POP_JUMP_IF_NONE', 'POP_JUMP_IF_NOT_NONE',
    'JUMP_FORWARD', 'JUMP_BACKWARD', 'JUMP_BACKWARD_NO_INTERRUPT',
    'FOR_ITER', 'END_FOR', 'POP_ITER', 'YIELD_VALUE',
    'RAISE_VARARGS', 'DELETE_FAST', 'DELETE_NAME', 'DELETE_ATTR',
    'DELETE_SUBSCR',
    'PUSH_EXC_INFO', 'CHECK_EXC_MATCH', 'POP_EXCEPT', 'RERAISE',
})


def _cmp_op(instr):
    """Extract comparison operator string from a COMPARE_OP instruction."""
    r = instr.argrepr
    if r.startswith('bool(') and r.endswith(')'):
        return r[5:-1]
    return r


def _is_empty_body(insts, code_obj, start=0):
    """Check if instruction list represents an empty body (just `pass`)."""
    relevant = [i for i in insts[start:] if i.opname not in ('CACHE', 'NOT_TAKEN')]
    if len(relevant) == 2:
        if (relevant[0].opname == 'LOAD_CONST'
            and code_obj.co_consts[relevant[0].arg] is None
            and relevant[1].opname == 'RETURN_VALUE'):
            return True
        if relevant[0].opname == 'RETURN_VALUE' and relevant[1].opname == 'RETURN_VALUE':
            return True  # Bare `return` with None
    return False


def _read_expression(insts, start, code_obj):
    """Read a single expression from the bytecode instruction stream.

    Simulates the Python 3.12 stack machine to reconstruct an expression
    string. Starts at ``start`` and continues until a statement boundary
    instruction (RETURN_VALUE, POP_JUMP_IF_*, STORE_*, etc.) or until
    the instruction list is exhausted.

    Returns:
        (expression_string, next_instruction_index)
        Returns (None, start) if reconstruction fails.
    """
    stack = []
    i = start

    while i < len(insts):
        instr = insts[i]
        op = instr.opname

        # Skip noise instructions
        if op in ('RESUME', 'CACHE', 'NOT_TAKEN', 'EXTENDED_ARG', 'MAKE_CELL',
                  'LOAD_LOCALS', 'STORE_DEREF', 'DELETE_DEREF'):
            i += 1
            continue

        # Statement boundaries — return ALL remaining stack values
        # (not just TOS) because statement handlers like STORE_ATTR
        # and STORE_SUBSCR need multiple values from the expression
        # stack (e.g. obj + val for self.x = val). If we only return
        # TOS, all bottom-of-stack values (function call results, etc.)
        # are silently discarded, producing 'self.attr = self' bugs.
        if op in _STATEMENT_BOUNDARY_OPS:
            if stack:
                return stack, i
            return [], i

        # ── Stack push operations ──
        if op == 'LOAD_CONST':
            stack.append(fmt_const(code_obj.co_consts[instr.arg]))
        elif op == 'LOAD_SMALL_INT':
            stack.append(str(instr.arg))
        elif op in ('LOAD_FAST', 'LOAD_FAST_BORROW'):
            stack.append(code_obj.co_varnames[instr.arg])
        elif op in ('LOAD_NAME', 'LOAD_GLOBAL'):
            # Strip ' + NULL' suffix from LOAD_GLOBAL (Python 3.12+ optimization)
            name = instr.argrepr.replace(' + NULL', '')
            stack.append(name)
        elif op == 'LOAD_DEREF':
            names = list(code_obj.co_freevars) + list(code_obj.co_cellvars)
            if instr.arg < len(names):
                stack.append(names[instr.arg])
        # Python 3.12+ combined superinstructions that load multiple values.
        # Only match pure load-combined ops (LOAD_*_LOAD_*), not
        # STORE_FAST_LOAD_FAST which has different stack semantics.
        elif (op.startswith('LOAD_') and '_LOAD_' in op
              and instr.argrepr and ', ' in instr.argrepr):
            # e.g. LOAD_FAST_BORROW_LOAD_FAST_BORROW with argrepr='x, y'
            for part in instr.argrepr.split(', '):
                stack.append(part)

        # ── Stack operations (pop, transform, push) ──
        elif op == 'COPY':
            # Duplicate top-of-stack items. Used by exception handling and
            # ``with`` statements. Arg = number of items below TOS to copy.
            if stack:
                stack.append(stack[-1])
        elif op == 'CONTAINS_OP':
            # ``in`` / ``not in`` operator. Pops two items, pushes comparison.
            if len(stack) >= 2:
                right = stack.pop()
                left = stack.pop()
                op_str = instr.argrepr if instr.argrepr else ('in' if instr.arg == 0 else 'not in')
                stack.append(f'{left} {op_str} {right}')
        elif op == 'IS_OP':
            # ``is`` / ``is not`` operator. Pops two items, pushes comparison.
            if len(stack) >= 2:
                right = stack.pop()
                left = stack.pop()
                op_str = 'is not' if instr.arg else 'is'
                stack.append(f'{left} {op_str} {right}')
        elif op == 'LOAD_SPECIAL':
            # Load special internal variables (Python 3.14+).
            # e.g. __classdict__, __class__ — argrepr gives the name.
            name = instr.argrepr if instr.argrepr else f'__special_{instr.arg}__'
            stack.append(name)
        elif op in ('LOAD_FAST_AND_CLEAR', 'LOAD_FAST_CHECK'):
            # LOAD_FAST variant for exception handling (clear after except)
            # and unbound-local checking. Same semantics as LOAD_FAST.
            name = instr.argrepr if instr.argrepr else code_obj.co_varnames[instr.arg]
            stack.append(name)
        elif op == 'LOAD_COMMON_CONSTANT':
            # Load common constants efficiently (Python 3.14+).
            # Arg: 0=None, 1=True, 2=False, 3=Ellipsis
            common = {0: 'None', 1: 'True', 2: 'False', 3: '...'}
            stack.append(common.get(instr.arg, f'__common_{instr.arg}__'))
        elif op == 'BINARY_SLICE':
            # Slice expression: container[start:stop] or container[start:stop:step]
            # Arg = number of slice components (2 or 3)
            if instr.arg == 2 and len(stack) >= 3:
                stop = stack.pop()
                start = stack.pop()
                container = stack.pop()
                stack.append(f'{container}[{start}:{stop}]')
            elif instr.arg == 3 and len(stack) >= 4:
                step = stack.pop()
                stop = stack.pop()
                start = stack.pop()
                container = stack.pop()
                stack.append(f'{container}[{start}:{stop}:{step}]')
        elif op == 'CALL_KW':
            # Call with keyword arguments.
            # Low byte = positional args, high byte = keyword args.
            # Stack layout: ..., callable, pos_args..., kw_values..., kw_names_tuple
            n_pos = instr.arg & 0xFF
            n_kw = (instr.arg >> 8) & 0xFF
            args = []
            # Pop keyword names tuple (top of stack) — discard since parsing
            # the reconstructed string representation is fragile.
            if stack and n_kw > 0:
                stack.pop()
            # Pop keyword values
            for _ in range(n_kw):
                if stack:
                    args.insert(0, stack.pop())
            # Pop positional args
            for _ in range(n_pos):
                if stack:
                    args.insert(0, stack.pop())
            if stack:
                func = stack.pop()
                func = func.replace(' + NULL', '')
                stack.append(f'{func}({", ".join(args)})')
        elif op in ('FORMAT_SIMPLE', 'FORMAT_WITH_SPEC'):
            # F-string formatting — value stays on stack for BUILD_STRING
            pass
        elif op == 'LOAD_FAST_LOAD_FAST':
            # Python 3.14 combined instruction loading TWO locals.
            # argrepr format: 'var1, var2'
            names = instr.argrepr if instr.argrepr else ''
            if ', ' in names:
                for part in names.split(', '):
                    stack.append(part)
            else:
                stack.append(names)
        elif op == 'NOP':
            pass  # No operation
        elif op == 'LOAD_ATTR':
            if stack:
                obj = stack.pop()
                # Use argrepr from dis.Bytecode() which correctly handles the
                # Python 3.12+ flag-bit encoding in LOAD_ATTR's arg field.
                # Direct indexing via code_obj.co_names[instr.arg] is wrong
                # because the low bit of arg is a method/attribute flag.
                attr_name = instr.argrepr if instr.argrepr else code_obj.co_names[instr.arg]
                # Strip Python 3.14 bound-method optimization annotations from argrepr.
                # When LOAD_ATTR loads a method for an upcoming CALL, the argrepr
                # may include suffixes like ' + NULL|self' or ' + NULL' that describe
                # the stack layout (NULL sentinel + self) for the bound method call.
                # These must be stripped to produce valid source code.
                attr_name = attr_name.replace(' + NULL|self', '').replace(' + NULL', '')
                stack.append(f'{obj}.{attr_name}')
        elif op == 'BINARY_OP':
            if instr.arg == _BINARY_OP_SUBSCR:
                # Subscript: container[key] (Python 3.14+ consolidated BINARY_SUBSCR)
                if len(stack) >= 2:
                    key = stack.pop()
                    container = stack.pop()
                    stack.append(f'{container}[{key}]')
            elif instr.arg in _BINARY_OP_INPLACE_TO_BASE:
                # In-place operators: x += 2 → decompile as `x = x + 2`
                # Pushes the binary expression `left base_op right` so that
                # the subsequent STORE_FAST emits `x = x + 2`.
                if len(stack) >= 2:
                    right = stack.pop()
                    left = stack.pop()
                    base_op = _BINARY_OP_INPLACE_TO_BASE[instr.arg]
                    stack.append(f'{left} {base_op} {right}')
            else:
                if len(stack) >= 2:
                    right = stack.pop()
                    left = stack.pop()
                    op_name = _BINARY_OP_NAMES.get(instr.arg, '?')
                    stack.append(f'{left} {op_name} {right}')
        elif op == 'UNARY_NEGATIVE':
            if stack:
                stack.append(f'-{stack.pop()}')
        elif op == 'UNARY_NOT':
            if stack:
                stack.append(f'not {stack.pop()}')
        elif op == 'TO_BOOL':
            pass  # Coercion to bool — transparent in source
        elif op == 'COMPARE_OP':
            if len(stack) >= 2:
                right = stack.pop()
                left = stack.pop()
                stack.append(f'{left} {_cmp_op(instr)} {right}')
        elif op == 'CALL':
            n_pos = instr.arg & 0xFF  # Low byte = positional arg count
            args = []
            for _ in range(n_pos):
                if stack:
                    args.insert(0, stack.pop())
            if stack:
                func = stack.pop()
                # Clean up ' + NULL' suffix from LOAD_GLOBAL
                func = func.replace(' + NULL', '')
                stack.append(f'{func}({", ".join(args)})')
        elif op == 'BUILD_LIST':
            items = []
            for _ in range(instr.arg):
                if stack:
                    items.insert(0, stack.pop())
            stack.append(f'[{", ".join(items)}]')
        elif op == 'LIST_EXTEND':
            pass  # Part of BUILD_LIST — handled above
        elif op == 'BUILD_TUPLE':
            n = instr.arg
            if n == 0:
                stack.append('()')
            else:
                items = []
                for _ in range(n):
                    if stack:
                        items.insert(0, stack.pop())
                if len(items) == 1:
                    stack.append(f'({items[0]},)')
                else:
                    stack.append(f'({", ".join(items)})')
        elif op == 'BUILD_SET':
            items = []
            for _ in range(instr.arg):
                if stack:
                    items.insert(0, stack.pop())
            stack.append('{' + ', '.join(items) + '}')
        elif op == 'BUILD_MAP':
            kv_pairs = []
            for _ in range(instr.arg):
                if len(stack) >= 2:
                    val = stack.pop()
                    key = stack.pop()
                    kv_pairs.insert(0, f'{key}: {val}')
            if kv_pairs:
                stack.append('{' + ', '.join(kv_pairs) + '}')
            else:
                stack.append('{}')
        elif op == 'BUILD_STRING':
            parts = []
            for _ in range(instr.arg):
                if stack:
                    parts.insert(0, stack.pop())
            # Reconstruct f-string: string literal parts are used directly
            # (quotes stripped), expression parts are wrapped in { }.
            f_parts = []
            quote_char = "'"
            for p in parts:
                if (p.startswith("'") and p.endswith("'")):
                    inner = p[1:-1]
                    # Preserve escaped quotes
                    inner = inner.replace("\\'", "'")
                    f_parts.append(inner)
                elif (p.startswith('"') and p.endswith('"')):
                    inner = p[1:-1]
                    inner = inner.replace('\\"', '"')
                    f_parts.append(inner)
                    quote_char = '"'
                else:
                    # Expression — wrap in braces
                    f_parts.append(f'{{{p}}}')
            stack.append(f'f{quote_char}{"".join(f_parts)}{quote_char}')
        elif op == 'FORMAT_VALUE':
            # Part of f-string — keep the expression on stack
            pass
        elif op == 'SWAP':
            if len(stack) >= 2:
                stack[-1], stack[-2] = stack[-2], stack[-1]
        elif op == 'GET_ITER':
            if stack:
                it = stack.pop()
                stack.append(f'iter({it})')
        elif op == 'PUSH_NULL':
            # Null pointer for bound method calls — transparent
            pass
        elif op == 'SET_ADD':
            # Set comprehension — pops TOS (value to add), set stays below.
            # For expression reconstruction, discard the value since the
            # comprehension is handled as a function call.
            if stack:
                stack.pop()
        elif op == 'IMPORT_NAME':
            # import X — pops fromlist + level, pushes module object.
            # Stack before: ..., fromlist (tuple or None), level (int)
            # Stack after: ..., module_object
            # Pop the two consumed values from simulated stack
            if stack:
                stack.pop()  # level
            if stack:
                stack.pop()  # fromlist
            mod_name = instr.argrepr if instr.argrepr else code_obj.co_names[instr.arg]
            mod_name = mod_name.replace(' + NULL', '')
            stack.append(f'__import__("{mod_name}")')
        elif op == 'IMPORT_FROM':
            # from X import Y — pushes attribute reference.
            # Stack before: ..., module
            # Stack after: ..., module, attribute
            attr_name = instr.argrepr if instr.argrepr else code_obj.co_names[instr.arg]
            if stack:
                module = stack[-1]
                stack.append(f'{module}.{attr_name}')
            else:
                stack.append(attr_name)
        else:
            # Unknown opcode — stop and return what we have
            break

        i += 1

    if stack:
        return stack, i
    return [], start


# Map jump offsets from the original bytecode to indices in the filtered list
# Used by decompile_body to resolve control flow targets.


def _build_reachable_set(insts, code_obj):
    """Build a set of instruction indices that are reachable from execution start.

    Performs jump-based control flow analysis starting from instruction 0 and
    all PUSH_EXC_INFO handler entry points (which are reachable via Python's
    exception table, not bytecode jumps). Follows:

    - Fall-through: most instructions lead to the next instruction
    - Unconditional jumps: ``JUMP_FORWARD``, ``JUMP_BACKWARD``, etc.
    - Conditional jumps: both taken and fall-through paths
    - Exception handler entry points: ``PUSH_EXC_INFO`` instructions

    Instructions NOT in the reachable set are dead code — they cannot be
    reached from any valid execution path and should not produce output.
    This includes code after ``RETURN_VALUE`` / ``RERAISE`` / ``RAISE_VARARGS``
    and cleanup instructions between exception handler groups.
    """
    offset_to_idx = {inst.offset: j for j, inst in enumerate(insts)}
    reachable = set()

    # Start from instruction 0 AND all try/except handler entry points
    # (PUSH_EXC_INFO). Exception handlers can be entered via the runtime's
    # exception table even though no bytecode instruction jumps to them.
    # Skip PUSH_EXC_INFO for ``with`` statement cleanup handlers (followed
    # by WITH_EXCEPT_START), matching the filter in _build_handler_map.
    push_indices = [
        j for j, inst in enumerate(insts)
        if inst.opname == 'PUSH_EXC_INFO'
        and (j + 1 >= len(insts) or insts[j + 1].opname != 'WITH_EXCEPT_START')
    ]
    stack = [0] + push_indices

    while stack:
        i = stack.pop()
        if i in reachable or i >= len(insts):
            continue
        reachable.add(i)

        inst = insts[i]
        op = inst.opname

        # Unconditional exits — no further reachability
        if op in ('RETURN_VALUE', 'RETURN_CONST', 'RERAISE', 'RAISE_VARARGS'):
            continue

        # Unconditional jumps — only the target is reachable
        if op in ('JUMP_FORWARD', 'JUMP_BACKWARD', 'JUMP_BACKWARD_NO_INTERRUPT'):
            target = offset_to_idx.get(inst.arg)
            if target is not None and target < len(insts):
                stack.append(target)
            continue

        # Conditional jumps — both taken and fall-through paths
        if op in ('POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE',
                   'POP_JUMP_IF_NONE', 'POP_JUMP_IF_NOT_NONE',
                   'FOR_ITER'):
            target = offset_to_idx.get(inst.arg)
            if target is not None and target < len(insts):
                stack.append(target)
            stack.append(i + 1)
            continue

        # Everything else: fall through to next instruction
        stack.append(i + 1)

    return reachable


def _build_exception_table(code_obj):
    """Parse ``co_exceptiontable`` and return a dict mapping
    handler target offsets to ``(try_start, try_end, depth)``.

    Uses ``dis._parse_exception_table()`` (Python 3.14+) which returns
    ``_ExceptionTableEntry`` objects with ``start``, ``end``, ``target``,
    ``depth``, and ``lasti`` fields. Each entry describes a try/except
    region: instructions from ``start`` to ``end`` (exclusive) are protected
    and an exception there transfers control to ``target``.

    Returns:
        dict mapping target_offset (int) -> (try_start, try_end, depth)
        Empty dict if ``co_exceptiontable`` is empty or unavailable.
    """
    result = {}
    try:
        if dis is not None and hasattr(dis, '_parse_exception_table'):
            entries = dis._parse_exception_table(code_obj)
            for entry in entries:
                result[entry.target] = (entry.start, entry.end, entry.depth)
    except Exception:
        pass
    return result


def _build_handler_map(insts, code_obj, offset_to_idx):
    """Pre-scan instructions to find try/except handler boundaries.

    Python 3.14 bytecode for try/except uses PUSH_EXC_INFO to mark
    the start of each exception handler. Multiple consecutive PUSH_EXC_INFO
    entries belong to the same try block (multiple except clauses).

    Uses ``co_exceptiontable`` (via ``_build_exception_table()``) to get
    accurate try body boundaries from Python's runtime exception table,
    replacing the heuristic ``prev_group_end + 1`` fallback.

    Args:
        insts: Filtered list of instructions.
        code_obj: The code object (for ``co_exceptiontable``).
        offset_to_idx: Mapping from raw bytecode offset to filtered instruction index.

    Returns:
        dict mapping instruction index -> handler info dict:
        {
            'try_start': int,    # index of try body start in insts
            'try_end': int,      # index of try body end (exclusive) in insts
            'push_idx': int,     # index of PUSH_EXC_INFO in insts
            'exc_type': str,     # exception type name (e.g. 'Exception')
            'exc_var': str|None, # exception variable name (e.g. 'e') or None
            'body_start': int,   # index of except body start in insts
            'handler_end': int,  # index of POP_EXCEPT in insts
        }
    """
    handler_map = {}
    i = 0
    # Track the end of the previous handler group so we can set try_start
    # for the next group correctly.
    prev_group_end = -1
    # Parse exception table once (not per handler group)
    exc_table = _build_exception_table(code_obj)

    while i < len(insts):
        if insts[i].opname != 'PUSH_EXC_INFO':
            i += 1
            continue

        # Skip PUSH_EXC_INFO for `with` statements (followed by
        # WITH_EXCEPT_START, not CHECK_EXC_MATCH). These are internal
        # exception handlers for context manager cleanup, not try/except
        # clauses, and should not be emitted as `except:` in output.
        next_idx = i + 1
        if next_idx < len(insts) and insts[next_idx].opname == 'WITH_EXCEPT_START':
            i = next_idx + 1
            continue

        # Found a handler group starting at index i
        group_start = i
        group_handlers = []

        # Scan forward through all except clauses in this group
        j = i
        while j < len(insts):
            cur = insts[j]
            if cur.opname == 'PUSH_EXC_INFO':
                # Skip `with` statement exception handlers here too
                if j + 1 < len(insts) and insts[j + 1].opname == 'WITH_EXCEPT_START':
                    j += 2
                    continue
                # Scan this single handler
                exc_type = 'Exception'
                exc_var = None
                body_start = None
                handler_end = None
                preamble_phase = True

                k = j + 1
                while k < len(insts):
                    c = insts[k]

                    # Phase 1: Handler preamble (exception type + variable setup)
                    if preamble_phase:
                        if c.opname in ('COPY', 'PUSH_NULL', 'LOAD_COMMON_CONSTANT'):
                            k += 1
                            continue
                        elif c.opname == 'LOAD_GLOBAL':
                            exc_type = c.argrepr.replace(' + NULL', '')
                        elif c.opname == 'LOAD_ATTR':
                            # Qualified exception name: sqlite3.DatabaseError
                            exc_type = f'{exc_type}.{c.argrepr}'
                        elif c.opname == 'LOAD_FAST':
                            # Local variable as exception type (rare)
                            exc_type = code_obj.co_varnames[c.arg]
                        elif c.opname == 'CHECK_EXC_MATCH':
                            pass
                        elif c.opname == 'POP_JUMP_IF_FALSE':
                            # End of preamble (bare except without 'as var')
                            if exc_var is None:
                                body_start = k + 1
                            preamble_phase = False
                        elif c.opname == 'STORE_FAST':
                            exc_var = code_obj.co_varnames[c.arg]
                            body_start = k + 1
                            preamble_phase = False
                    else:
                        # Phase 2: Handler body — look for handler end
                        if c.opname == 'STORE_FAST' and exc_var is None:
                            # STORE_FAST after POP_JUMP_IF_FALSE
                            # (except ExcType as var:)
                            exc_var = code_obj.co_varnames[c.arg]
                            body_start = k + 1
                        elif c.opname == 'POP_EXCEPT':
                            handler_end = k
                            break
                        elif c.opname in ('RETURN_VALUE', 'RERAISE'):
                            if body_start is None:
                                body_start = k
                            break
                        # PUSH_EXC_INFO in body = NESTED try/except — skip it
                        # All other instructions are body code — pass through

                    k += 1

                if body_start is None:
                    body_start = j + 1

                if body_start is None:
                    body_start = j + 1

                group_handlers.append((j, exc_type, exc_var, body_start, handler_end))
                j = k if k > j else j + 1
                continue

            elif cur.opname in ('JUMP_FORWARD', 'RERAISE', 'RAISE_VARARGS',
                                'DELETE_FAST', 'LOAD_FAST_AND_CLEAR',
                                'LOAD_CONST', 'STORE_FAST', 'STORE_DEREF',
                                'DELETE_DEREF', 'POP_JUMP_IF_TRUE',
                                'POP_JUMP_IF_FALSE', 'COPY'):
                # Cleanup instructions between handler group and next code
                j += 1
                continue
            else:
                # Reached non-handler, non-cleanup instruction
                break

        # Look up accurate try_start/try_end from the exception table.
        # Uses ``co_exceptiontable`` (via ``_build_exception_table()``) to
        # get the exact try body range that Python's runtime associates
        # with this handler group. Falls back to heuristic if the table
        # has no mapping for this PUSH_EXC_INFO offset.
        def _lookup_try_range(exc_table, push_offset):
            """Return (try_start, try_end) from exception table, or (None, None)
            for handlers not in the table (e.g. nested with-statement cleanup)."""
            if push_offset in exc_table:
                raw_start, raw_end, _ = exc_table[push_offset]
                # Convert raw byte offsets to filtered instruction indices
                ts = offset_to_idx.get(raw_start)
                te = offset_to_idx.get(raw_end)
                if ts is not None and te is not None and ts < te:
                    return ts, te
            return None, None

        first_push_offset = insts[group_handlers[0][0]].offset
        ts, te = _lookup_try_range(exc_table, first_push_offset)

        if ts is not None and te is not None:
            # Use accurate boundary from exception table
            try_start = ts
            try_end = te
            # If prev_group_end is later than accurate try_start, keep
            # the later start to avoid overlap with previous handler code
            if prev_group_end >= 0 and try_start <= prev_group_end:
                try_start = prev_group_end + 1
                if try_start >= try_end:
                    try_start = prev_group_end + 1
                    try_end = try_start + 1
        else:
            # Fallback: heuristic from prev_group_end + 1
            try_start = prev_group_end + 1 if prev_group_end >= 0 else 0
            try_end = None

        # group_end = first instruction after ALL handlers + cleanup code.
        # This is where the outer scan continues (j), past cleanup instructions
        # like DELETE_FAST, LOAD_FAST_AND_CLEAR that must be skipped in
        # decompile_body to avoid spurious `var = ?` assignments.
        group_end = j

        for push_idx, exc_type, exc_var, body_start, handler_end in group_handlers:
            handler_map[push_idx] = {
                'try_start': try_start,
                'try_end': try_end,
                'push_idx': push_idx,
                'exc_type': exc_type,
                'exc_var': exc_var,
                'body_start': body_start,
                'handler_end': handler_end,
                'group_end': group_end,
            }

        # Set prev_group_end so the next group's try_start starts at
        # the first non-cleanup instruction (group_end). Previously used
        # handler_end, which left cleanup instructions inside the next
        # try body, causing spurious assignments.
        if group_handlers:
            prev_group_end = group_end - 1

        i = j  # Continue scanning from where we left off

    return handler_map


def _try_emit_import(name, val, prefix):
    """Check if *val* is a reconstructed ``__import__()`` pattern and return
    the idiomatic import line. Returns ``None`` if no import pattern is detected.

    Handles:
    - ``__import__(\"X\")`` + ``name == X`` → ``import X``
    - ``__import__(\"X\")`` + ``name != X`` → ``import X as name``
    - ``__import__(\"X\").Y`` + ``name == Y`` → ``from X import Y``
    - ``__import__(\"X\").Y`` + ``name != Y`` → ``from X import Y as name``

    Used by both ``STORE_FAST`` and ``STORE_NAME``/``STORE_GLOBAL`` handlers
    in ``decompile_body()`` to convert the reconstructed ``__import__()``
    expression back into idiomatic ``import`` / ``from`` statements.
    """
    import_match = re.match(r'^__import__\("([^"]+)"\)$', val)
    if import_match:
        mod_name = import_match.group(1)
        if name == mod_name:
            return f'{prefix}import {mod_name}'
        else:
            return f'{prefix}import {mod_name} as {name}'
    from_match = re.match(r'^__import__\("([^"]+)"\)\.([A-Za-z_]\w*)$', val)
    if from_match:
        mod_name = from_match.group(1)
        attr_name = from_match.group(2)
        if attr_name == name:
            return f'{prefix}from {mod_name} import {attr_name}'
        else:
            return f'{prefix}from {mod_name} import {attr_name} as {name}'
    return None


def decompile_body(code_obj, indent=4, insts=None, is_class_body=False):

    """Decompile a code object's body into Python source statements.

    Handles:
    - Empty bodies (``pass``)
    - ``return <expr>`` statements
    - ``<var> = <expr>`` assignments
    - ``if <cond>:`` / ``elif <cond>:`` / ``else:`` blocks (with ``return`` bodies)
    - ``for <var> in <iterable>:`` loops
    - Simple call expressions as statements (e.g. ``print(x)``)

    Args:
        code_obj: The code object to decompile.
        indent: Number of spaces to indent each line.
        insts: Optional pre-filtered instruction list (for sub-blocks).
               If provided, ``code_obj`` metadata (co_consts, co_varnames)
               is still used.
        is_class_body: When True, suppress ``RETURN_VALUE`` / ``RETURN_CONST``
               instructions. Class body functions in Python 3.12+ end with
               an implicit ``return None`` that is NOT valid source code at
               class body level.

    Returns:
        list of source lines with the given indentation.
    """
    if dis is None:
        return [f'{" " * indent}pass']

    if insts is None:
        insts = list(dis.Bytecode(code_obj))
        # Filter noise instructions
        insts = [i for i in insts
                 if i.opname not in ('RESUME', 'CACHE', 'NOT_TAKEN', 'EXTENDED_ARG')]

    if not insts:
        return [f'{" " * indent}pass']

    # Build prefix first so empty-body checks can use it
    prefix = ' ' * indent

    # ── Empty body check ──
    if _is_empty_body(insts, code_obj):
        return [f'{prefix}pass']

    # ── Build jump target map ──
    targets = {}
    for inst in insts:
        if inst.opname in ('POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE',
                           'POP_JUMP_IF_NONE', 'POP_JUMP_IF_NOT_NONE',
                           'JUMP_FORWARD', 'JUMP_BACKWARD',
                           'JUMP_BACKWARD_NO_INTERRUPT', 'FOR_ITER'):
            target = inst.arg
            if target not in targets and target != 0:
                targets[target] = f'L{len(targets)}'

    offset_to_idx = {inst.offset: j for j, inst in enumerate(insts)}

    # ── Build exception handler map (for try/except reconstruction) ──
    decompile_body._handler_map = _build_handler_map(insts, code_obj, offset_to_idx)

    # ── Build reachable set from jump-based control flow analysis ──
    # Instructions not in the reachable set are dead code (unreachable
    # from any valid execution path). The reachability analysis follows
    # jumps, fall-through, and exception handler entry points.
    # This replaces the ad-hoc dead-code skips in individual handlers
    # (RETURN_VALUE, RERAISE, RAISE_VARARGS) with a principled analysis
    # that catches ALL dead code paths, including edge cases after
    # exception handler fall-through paths.
    reachable = _build_reachable_set(insts, code_obj)

    # ── Decompile body ──
    lines = []
    expr_stack = []
    i = 0

    while i < len(insts):
        instr = insts[i]
        op = instr.opname

        # ── Skip dead code (instructions not reachable via jumps) ──
        # This is the primary dead-code filter. Individual handlers
        # (RETURN_VALUE, RERAISE, RAISE_VARARGS) also skip dead code
        # as a secondary measure, but the reachability analysis is
        # the authoritative source.
        if i not in reachable:
            i += 1
            continue

        # Check for jump target labels (only for reachable instructions)
        if instr.offset in targets:
            label = targets[instr.offset]
            lines.append(f'{prefix}# -> {label}')

        # ── Return statements ──
        # After an unconditional return, skip forward past dead code
        # (instructions between the return and the next jump target label).
        if op in ('RETURN_VALUE', 'RETURN_CONST'):
            # Class bodies in Python 3.12+ end with an implicit
            # RETURN_CONST None that is NOT valid source code.
            # Suppress it entirely when decompiling a class body.
            # The class body function's return value is never visible
            # in source — it's a compiler implementation detail.
            if is_class_body:
                j = i + 1
                while j < len(insts):
                    if insts[j].offset in targets:
                        break
                    j += 1
                i = j
                continue
            if expr_stack:
                val = expr_stack.pop()
                lines.append(f'{prefix}return {val}')
            else:
                lines.append(f'{prefix}return')
            # Skip dead code after return
            j = i + 1
            while j < len(insts):
                if insts[j].offset in targets:
                    break
                j += 1
            i = j
            continue

        # ── Pop top (expression statement) ──
        if op == 'POP_TOP':
            if expr_stack:
                val = expr_stack.pop()
                # Silently consume leftover module reference from ``from X import Y``
                # patterns (the ``__import__("X")`` result is popped after IMPORT_FROM
                # processing, but the module object stays on the simulated stack and
                # eventually reaches POP_TOP). Don't emit it as a statement.
                if re.match(r'^__import__\("([^"]+)"\)$', val):
                    pass
                elif '(' in val and val.rstrip(')') != val:
                    lines.append(f'{prefix}{val}')
            i += 1
            continue

        # ── Assignment: local variable ──
        # (also handles reconstructed ``import X`` in function bodies)
        if op == 'STORE_FAST':
            name = code_obj.co_varnames[instr.arg]
            val = expr_stack.pop() if expr_stack else 'None'
            import_line = _try_emit_import(name, val, prefix)
            if import_line is not None:
                lines.append(import_line)
                i += 1
                continue
            lines.append(f'{prefix}{name} = {val}')
            i += 1
            continue

        # ── Assignment: global/name ──
        # (also handles reconstructed ``import X`` and ``from X import Y``)
        if op in ('STORE_NAME', 'STORE_GLOBAL'):
            name = code_obj.co_names[instr.arg]
            val = expr_stack.pop() if expr_stack else 'None'
            import_line = _try_emit_import(name, val, prefix)
            if import_line is not None:
                lines.append(import_line)
                i += 1
                continue
            lines.append(f'{prefix}{name} = {val}')
            i += 1
            continue

        # ── Assignment: attribute ──
        if op == 'STORE_ATTR':
            if len(expr_stack) >= 2:
                obj = expr_stack.pop()
                val = expr_stack.pop()
                attr = code_obj.co_names[instr.arg]
                lines.append(f'{prefix}{obj}.{attr} = {val}')
            i += 1
            continue

        # ── Assignment: subscript ──
        if op == 'STORE_SUBSCR':
            if len(expr_stack) >= 3:
                val = expr_stack.pop()
                key = expr_stack.pop()
                obj = expr_stack.pop()
                lines.append(f'{prefix}{obj}[{key}] = {val}')
            i += 1
            continue

        # ── Combined store+load: STORE_FAST_LOAD_FAST ──
        if op == 'STORE_FAST_LOAD_FAST':
            load_name = instr.argrepr if instr.argrepr else ''
            if ', ' in load_name:
                parts = load_name.split(', ')
                store_name = parts[0]
                load_name2 = parts[1] if len(parts) > 1 else ''
            else:
                store_name = load_name
                load_name2 = ''
            val = expr_stack.pop() if expr_stack else '?'
            lines.append(f'{prefix}{store_name} = {val}')
            if load_name2:
                expr_stack.append(load_name2)
            i += 1
            continue

        # ── Combined store+store: STORE_FAST_STORE_FAST ──
        if op == 'STORE_FAST_STORE_FAST':
            load_name = instr.argrepr if instr.argrepr else ''
            if ', ' in load_name:
                parts = load_name.split(', ')
                name2 = parts[1] if len(parts) > 1 else 'None'
                name1 = parts[0]
            else:
                name1 = load_name
                name2 = 'None'
            val2 = expr_stack.pop() if expr_stack else 'None'
            val1 = expr_stack.pop() if expr_stack else 'None'
            lines.append(f'{prefix}{name1} = {val1}')
            lines.append(f'{prefix}{name2} = {val2}')
            i += 1
            continue

        # ── Tuple unpacking ──
        if op == 'UNPACK_SEQUENCE':
            # Pops a sequence value, then subsequent STORE_FAST instructions
            # consume individual items. We just skip this and let the stores
            # grab from the expression stack.
            i += 1
            continue

        # ── Exception handling ──
        # Check for pre-detected exception handler boundaries.
        # These are set by the handler pre-scan at the top of decompile_body().
        # Recursive decompile_body() calls overwrite the module-level
        # _handler_map attribute, so we must save/restore around them.
        if getattr(decompile_body, '_handler_map', None) and i in decompile_body._handler_map:
            handler = decompile_body._handler_map[i]
            handler_map_saved = decompile_body._handler_map

            # Collect ALL handlers in the same try group (same try_start)
            all_group_handlers = [
                h for h in decompile_body._handler_map.values()
                if h.get('try_start') == handler.get('try_start')
            ]
            # Sort by push_idx to maintain except clause order
            all_group_handlers.sort(key=lambda h: h['push_idx'])

            try_start = handler['try_start']
            first_push_idx = all_group_handlers[0]['push_idx']
            group_end = handler.get('group_end', None)

            # Emit try: once. Uses the ``try_end`` from the exception table
            # (``co_exceptiontable``) for an accurate try body range. If the
            # exception table is unavailable, falls back to the original
            # heuristic: if code was already processed by the main loop
            # (``try_start < i``), emit ``pass`` to avoid duplication.
            if try_start < first_push_idx:
                try_end = handler.get('try_end')
                if try_end is not None and try_end > try_start and try_start >= 0 and try_end <= len(insts):
                    # Accurate try body range from exception table —
                    # decompile the try body between try_start and try_end.
                    # Skip past try body in the main loop (i = try_end) so
                    # the expression stack stays correct.
                    try_insts = insts[try_start:try_end]
                    try_lines = decompile_body(code_obj, indent + 4, insts=try_insts)
                    if try_lines:
                        lines.append(f'{prefix}try:')
                        lines.extend(try_lines)
                        # Restore handler_map after recursive call
                        decompile_body._handler_map = handler_map_saved
                    i = try_end
                else:
                    # Fallback: code may already have been processed by
                    # the main loop (heuristic). Emit pass to avoid
                    # duplicating instructions that would break the
                    # expression stack and produce ``var = ?`` errors.
                    if try_start < i:
                        # Code already emitted by main loop — emit pass
                        lines.append(f'{prefix}try:')
                        lines.append(f'{prefix}    pass')
                    else:
                        try_insts = insts[try_start:first_push_idx]
                        try_lines = decompile_body(code_obj, indent + 4, insts=try_insts)
                        if try_lines:
                            lines.append(f'{prefix}try:')
                            lines.extend(try_lines)
                            # Restore handler_map after recursive call
                            decompile_body._handler_map = handler_map_saved

            # Emit all except clauses in this group
            for h in all_group_handlers:
                exc_type = h['exc_type']
                exc_var = h['exc_var']
                body_start = h.get('body_start', h['push_idx'] + 1)
                handler_end = h.get('handler_end')

                if exc_var:
                    lines.append(f'{prefix}except {exc_type} as {exc_var}:')
                else:
                    lines.append(f'{prefix}except {exc_type}:')

                if body_start is not None and handler_end is not None and body_start < handler_end:
                    body_insts = insts[body_start:handler_end]
                    body_lines = decompile_body(code_obj, indent + 4, insts=body_insts)
                    lines.extend(body_lines)
                    # Restore handler_map after recursive call
                    decompile_body._handler_map = handler_map_saved
                else:
                    lines.append(f'{prefix}    pass')

            # Skip past the ENTIRE group (all except clauses + cleanup code)
            if group_end is not None:
                i = group_end
            else:
                last_handler_end = all_group_handlers[-1].get('handler_end')
                i = last_handler_end + 1 if last_handler_end is not None else len(insts)
            continue

        if op == 'PUSH_EXC_INFO':
            # Fallback if no handler map was built (shouldn't happen)
            i += 1
            continue

        if op == 'POP_EXCEPT':
            i += 1
            continue

        if op == 'RERAISE':
            lines.append(f'{prefix}raise')
            # Skip dead code after re-raise (instructions between RERAISE
            # and the next jump target). Same pattern as RETURN_VALUE
            # and RAISE_VARARGS which skip forward past unreachable code
            # to avoid producing spurious `var = None` assignments from
            # exception-handler fall-through paths.
            j = i + 1
            while j < len(insts):
                if insts[j].offset in targets:
                    break
                j += 1
            i = j
            continue

        if op == 'RAISE_VARARGS':
            # Explicit raise statement.
            # Stack: ..., cause?, exc? — exc is on top (pushed last).
            # arg: 0=bare raise, 1=raise exc, 2=raise exc from cause
            if instr.arg == 2 and len(expr_stack) >= 2:
                exc = expr_stack.pop()
                cause = expr_stack.pop()
                lines.append(f'{prefix}raise {exc} from {cause}')
            elif instr.arg >= 1 and expr_stack:
                exc = expr_stack.pop()
                lines.append(f'{prefix}raise {exc}')
            else:
                lines.append(f'{prefix}raise')
            # Skip dead code after raise
            j = i + 1
            while j < len(insts):
                if insts[j].offset in targets:
                    break
                j += 1
            i = j
            continue

        if op == 'CHECK_EXC_MATCH':
            # Handled by PUSH_EXC_INFO block above
            i += 1
            continue

        # ── If / elif / else ──
        if op == 'POP_JUMP_IF_FALSE':
            cond = expr_stack.pop() if expr_stack else 'True'
            target_offset = instr.arg

            # Find then-body (instructions up to the jump target or JUMP_FORWARD)
            then_body, next_i = _extract_block(insts, i + 1, target_offset)

            then_lines = decompile_body(code_obj, indent + 4, insts=then_body)

            lines.append(f'{prefix}if {cond}:')
            lines.extend(then_lines)

            # Move to the target offset (where else/elif/fallthrough code starts)
            # The main loop continues from there
            i = next_i
            continue

        # ── If None / is not None ──
        if op == 'POP_JUMP_IF_NONE':
            val = expr_stack.pop() if expr_stack else 'None'
            target_offset = instr.arg
            then_body, next_i = _extract_block(insts, i + 1, target_offset)
            then_lines = decompile_body(code_obj, indent + 4, insts=then_body)
            lines.append(f'{prefix}if {val} is None:')
            lines.extend(then_lines)
            i = next_i
            continue

        if op == 'POP_JUMP_IF_NOT_NONE':
            val = expr_stack.pop() if expr_stack else 'None'
            target_offset = instr.arg
            then_body, next_i = _extract_block(insts, i + 1, target_offset)
            then_lines = decompile_body(code_obj, indent + 4, insts=then_body)
            lines.append(f'{prefix}if {val} is not None:')
            lines.extend(then_lines)
            i = next_i
            continue

        # ── For loops ──
        if op == 'GET_ITER':
            i += 1
            if i < len(insts) and insts[i].opname == 'FOR_ITER':
                loop_end_offset = insts[i].arg

                # Get the iterable from the stack
                iterable = expr_stack.pop() if expr_stack else 'None'

                # Next instruction(s) set the loop variable.
                # Handle two patterns:
                # 1. Simple: ``STORE_FAST var``  (e.g. ``for x in ...``)
                # 2. Tuple unpacking: ``UNPACK_SEQUENCE N`` + ``STORE_FAST_STORE_FAST``
                #    (e.g. ``for i, instr in enumerate(...)``)
                i += 1
                loop_var = 'None'
                if i < len(insts):
                    cur = insts[i]
                    if cur.opname == 'STORE_FAST':
                        loop_var = code_obj.co_varnames[cur.arg]
                        i += 1
                    elif cur.opname == 'UNPACK_SEQUENCE':
                        # Skip UNPACK_SEQUENCE, look for STORE_FAST_STORE_FAST
                        i += 1
                        if i < len(insts) and insts[i].opname == 'STORE_FAST_STORE_FAST':
                            # Use argrepr for variable names (e.g. 'i, instr')
                            loop_var = insts[i].argrepr or 'None'
                            i += 1

                # Extract loop body (up to the JUMP_BACKWARD)
                body_insts = []
                while i < len(insts):
                    cur = insts[i]
                    if cur.opname in ('JUMP_BACKWARD', 'JUMP_BACKWARD_NO_INTERRUPT'):
                        break
                    body_insts.append(cur)
                    i += 1

                body_lines = decompile_body(code_obj, indent + 4, insts=body_insts)

                lines.append(f'{prefix}for {loop_var} in {iterable}:')
                lines.extend(body_lines)

                # Skip loop bookkeeping
                if i < len(insts):
                    i += 1
                while i < len(insts) and insts[i].opname in ('END_FOR', 'POP_ITER'):
                    i += 1
            continue

        # ── Delete subscript ──
        if op == 'DELETE_SUBSCR':
            if len(expr_stack) >= 2:
                key = expr_stack.pop()
                obj = expr_stack.pop()
                lines.append(f'{prefix}del {obj}[{key}]')
            i += 1
            continue

        # ── Loop bookkeeping ──
        if op in ('FOR_ITER', 'END_FOR', 'POP_ITER',
                  'JUMP_BACKWARD', 'JUMP_BACKWARD_NO_INTERRUPT',
                  'JUMP_FORWARD'):
            i += 1
            continue

        # ── All other ops: push onto expression stack ──
        exprs, next_i = _read_expression(insts, i, code_obj)
        if exprs and next_i > i:
            expr_stack.extend(exprs)
            i = next_i
        else:
            i += 1

    if not lines:
        return [f'{prefix}pass']

    return lines


def _extract_block(insts, start_idx, end_offset):
    """Extract a block of instructions from start_idx up to end_offset
    or until a JUMP_FORWARD (indicating an else branch follows).

    Returns:
        (block_instructions, next_index_after_block)
    """
    block = []
    i = start_idx

    while i < len(insts):
        inst = insts[i]
        if inst.offset == end_offset:
            return block, i
        if inst.opname == 'JUMP_FORWARD':
            return block, i + 1
        block.append(inst)
        i += 1

    return block, i


_PROJECT_IMPORTS = {
    "CONFIG": ("config", "from config import CONFIG"),
    "get_logger": ("logging_config", "from logging_config import get_logger"),
    "get_provider": ("ai_base", "from ai_base import get_provider"),
    "PromptBuilder": ("prompt_builder", "from prompt_builder import PromptBuilder"),
    "ServiceRegistry": ("services", "from services import ServiceRegistry"),
    "get_services": ("services", "from services import get_services"),
    "PluginRegistry": ("plugin_registry", "from plugin_registry import PluginRegistry"),
}

# Standard library imports: maps simple module name -> import statement
_STDLIB_IMPORTS = {
    "os": "import os",
    "sys": "import sys",
    "json": "import json",
    "re": "import re",
    "time": "import time",
    "math": "import math",
    "sqlite3": "import sqlite3",
    "hashlib": "import hashlib",
    "uuid": "import uuid",
    "pathlib": "import pathlib",
    "threading": "import threading",
    "copy": "import copy",
    "struct": "import struct",
    "textwrap": "import textwrap",
    "logging": "import logging",
    "contextlib": "from contextlib import closing, contextmanager",
    "datetime": "from datetime import datetime, timedelta",
    "collections": "from collections import defaultdict, deque",
    "functools": "import functools",
    "typing": "from typing import Optional, List, Dict, Tuple, Any",
}


def _is_class(name: str) -> bool:
    """Detect if a code object name represents a class.

    Handles:
    - Standard classes: ``MemoryVault``, ``PluginRegistry``
    - Underscore-prefixed internal classes: ``_RequestsMissingStub``
    - Excludes: ``requests`` (module-level name collision), ``__annotate__``

    Uses ``lstrip('_')`` to strip leading underscores so that
    ``_RequestsMissingStub`` → ``RequestsMissingStub`` → ``'R'.isupper()`` = True.
    """
    if not name or name == "__annotate__":
        return False
    stripped = name.lstrip('_')
    # Handle all-underscore names like "__" or "___"
    if not stripped:
        return False
    return stripped[0].isupper()


def _get_docstring(code_obj):
    """Extract the docstring from a code object, handling Python 3.14 class layout.

    Python 3.14 stores class docstrings via ``STORE_NAME __doc__`` in the class
    body bytecode, NOT at ``co_consts[0]`` (which holds the class qualname).
    Functions still use ``co_consts[0]`` for docstrings.

    This function scans the bytecode for the ``STORE_NAME __doc__`` pattern
    and returns the string constant that precedes it. Falls back to
    ``co_consts[0]`` for non-class code objects and edge cases.
    """
    if dis is None:
        if code_obj.co_consts and isinstance(code_obj.co_consts[0], str):
            return code_obj.co_consts[0]
        return ""

    if _is_class(code_obj.co_name):
        # Classes: find __doc__ via STORE_NAME in bytecode
        try:
            prev_load_const = None
            for instr in dis.Bytecode(code_obj):
                if instr.opname == 'LOAD_CONST':
                    prev_load_const = instr
                elif (instr.opname == 'STORE_NAME'
                      and instr.argrepr == '__doc__'
                      and prev_load_const is not None):
                    val = code_obj.co_consts[prev_load_const.arg]
                    if isinstance(val, str) and len(val) > 3:
                        return val
        except Exception:
            pass
        return ""
    else:
        # Functions/modules: co_consts[0] is the standard docstring location
        if (code_obj.co_consts
            and isinstance(code_obj.co_consts[0], str)
            and len(code_obj.co_consts[0]) > 3):
            return code_obj.co_consts[0]
        return ""


def derive_module_name(pyc_path: str) -> str:
    """Extract the module name from a .pyc file path.

    Examples:
        __pycache__/ai_base.cpython-312.pyc  ->  ai_base
        memory_vault.cpython-312.pyc          ->  memory_vault
        C:/path/to/kokertechController.pyc   ->  kokertechController
    """
    basename = pathlib.Path(pyc_path).stem  # e.g. "ai_base.cpython-312"
    # Strip the .cpython-XXX suffix if present
    m = re.match(r"^(.+)\.cpython-\d+$", basename)
    if m:
        return m.group(1)
    # Also handle plain .pyc extension
    if basename.endswith(".pyc"):
        basename = basename[:-4]
    return basename


def load_code(path):
    with open(path, "rb") as f:
        data = f.read()
    hdr = 16
    return marshal.loads(data[hdr:])


# Decorator names detected at class-body bytecode level
_DECORATOR_NAMES = frozenset({'property', 'staticmethod', 'classmethod'})


def _find_method_call_in_class(instructions, start_idx):
    """Scan forward from start_idx to find the method definition + decorator call.

    Python 3.14 can produce two bytecode patterns for decorated methods:

    Simple (no __annotate__ closure):
        LOAD_CONST <code_object>
        MAKE_FUNCTION
        CALL 0
        STORE_NAME <name>

    Complex (with __annotate__ closure):
        LOAD_FAST_BORROW __classdict__
        BUILD_TUPLE 1
        LOAD_CONST <__annotate__ code>
        MAKE_FUNCTION
        SET_FUNCTION_ATTRIBUTE closure
        LOAD_CONST <code_object>
        MAKE_FUNCTION
        SET_FUNCTION_ATTRIBUTE annotate
        CALL 0
        STORE_NAME <name>

    Returns:
        (code_object, stmt_after_call_name) or (None, None) if not found.
    """
    # Scan up to 20 instructions ahead to find CALL
    end = min(start_idx + 20, len(instructions))
    j = start_idx
    last_method_code = None
    while j < end:
        inst = instructions[j]
        if inst.opname == 'LOAD_CONST' and isinstance(inst.argval, types.CodeType):
            last_method_code = inst.argval
        elif inst.opname == 'MAKE_FUNCTION':
            pass  # Skip MAKE_FUNCTION
        elif (inst.opname in ("CALL", "CALL_FUNCTION", "PRECALL")
              and last_method_code is not None
              and j + 1 < len(instructions)
              and instructions[j + 1].opname in ("STORE_NAME", "STORE_GLOBAL")):
            return last_method_code, instructions[j + 1].argrepr
        j += 1
    return None, None


def detect_decorators(class_code):
    """Detect class-level decorators in bytecode.

    Detects three patterns:

    Pattern 1: Simple decorators (@property, @staticmethod, @classmethod):
        LOAD_NAME/LOAD_GLOBAL <decorator>
        <optional closure/annotate setup>
        LOAD_CONST <code_object>
        MAKE_FUNCTION
        <optional SET_FUNCTION_ATTRIBUTE>
        CALL
        STORE_NAME <name>

    Pattern 2: Property setter/deleter (@x.setter, @x.deleter):
        LOAD_NAME/LOAD_GLOBAL <prop_name>
        LOAD_ATTR 'setter'/'deleter'
        <optional closure/annotate setup>
        LOAD_CONST <code_object>
        MAKE_FUNCTION
        <optional SET_FUNCTION_ATTRIBUTE>
        CALL
        STORE_NAME <name>

    Returns:
        dict mapping id(code_object) -> decorator string (e.g. '@property', '@bar.setter')
    """
    decorators = {}
    if dis is None:
        return decorators
    try:
        instructions = list(dis.Bytecode(class_code))

        # Pattern 1: @property, @staticmethod, @classmethod
        for i, instr in enumerate(instructions):
            if not (instr.opname in ("LOAD_NAME", "LOAD_GLOBAL")
                    and instr.argrepr in _DECORATOR_NAMES):
                continue

            code_obj, stmt_name = _find_method_call_in_class(instructions, i + 1)
            if code_obj is not None:
                decorators[id(code_obj)] = f'@{instr.argrepr}'

        # Pattern 2: @prop.setter, @prop.deleter
        for i, instr in enumerate(instructions):
            if not (instr.opname in ("LOAD_NAME", "LOAD_GLOBAL")):
                continue

            if i + 1 < len(instructions):
                next1 = instructions[i + 1]
                if next1.opname == "LOAD_ATTR" and next1.argrepr in ("setter", "deleter"):
                    code_obj, stmt_name = _find_method_call_in_class(instructions, i + 2)
                    if code_obj is not None:
                        decorators[id(code_obj)] = f'@{instr.argrepr}.{next1.argrepr}'
    except Exception:
        pass
    return decorators


def fmt_const(c):
    if c is None: return "None"
    if isinstance(c, bool): return "True" if c else "False"
    if isinstance(c, int): return str(c)
    if isinstance(c, float): return repr(c)
    if isinstance(c, str):
        return repr(c)
    if isinstance(c, bytes): return repr(c)
    if isinstance(c, types.CodeType):
        # Generator expressions and comprehensions produce nested code objects.
        # Emit a placeholder with the function name (e.g. '<genexpr>').
        return f'...  # {c.co_name}'
    if isinstance(c, tuple):
        if len(c) == 0: return "()"
        if len(c) == 1: return f"({fmt_const(c[0])},)"
        return "(" + ", ".join(fmt_const(x) for x in c) + ")"
    return repr(c)


def _deduplicate_methods(class_code):
    """Deduplicate methods in a class by keeping the code object with the most instructions.

    Python 3.14 (and 3.12+) may generate duplicate code objects in a class body's
    ``co_consts`` — one is a compiler-generated stub (simple body for closure setup)
    and the other is the actual method with the full body, docstring, etc.

    Strategy: for each method name, count filtered bytecode instructions and keep
    the code object with the highest count. True duplicates (same instruction count)
    keep the first occurrence.
    """
    if dis is None:
        return list(class_code.co_consts)
    
    groups = {}
    for cc in class_code.co_consts:
        if isinstance(cc, types.CodeType) and cc.co_name not in ("<module>", "__annotate__"):
            name = cc.co_name
            # Count filtered instructions (same filter as decompile_body)
            try:
                insts = list(dis.Bytecode(cc))
                filtered = [i for i in insts
                           if i.opname not in ('RESUME', 'CACHE', 'NOT_TAKEN', 'EXTENDED_ARG')]
                n_insts = len(filtered)
            except Exception:
                n_insts = 0
            
            if name not in groups:
                groups[name] = (cc, n_insts)
            else:
                existing_cc, existing_n = groups[name]
                if n_insts > existing_n:
                    groups[name] = (cc, n_insts)
    
    # Return deduplicated code objects in original order (preserve import ordering)
    seen = set()
    result = []
    for cc in class_code.co_consts:
        if isinstance(cc, types.CodeType) and cc.co_name not in ("<module>", "__annotate__"):
            name = cc.co_name
            if name not in seen:
                seen.add(name)
                # Emit the best version (highest instruction count)
                best_cc, _ = groups[name]
                result.append(best_cc)
        else:
            result.append(cc)
    return result


def get_params(code):
    args = []
    n = code.co_argcount
    kw = code.co_kwonlyargcount
    flags = code.co_flags
    va = bool(flags & 4)
    vk = bool(flags & 8)
    vns = list(code.co_varnames)
    defaults = getattr(code, 'co_defaults', ()) or ()
    n_no_default = n - len(defaults)
    for i in range(n):
        name = vns[i]
        di = i - n_no_default
        if 0 <= di < len(defaults):
            args.append(f"{name}={fmt_const(defaults[di])}")
        else:
            args.append(name)
    if va:
        args.append(f"*{vns[n+kw]}")
    elif kw > 0:
        args.append("*")
    for i in range(kw):
        args.append(vns[n + i])
    if vk:
        args.append(f"**{vns[-1]}")
    return ", ".join(args)


def reconstruct(code, module_name="", skip_properties=False):
    lines = []
    is_mod = code.co_name == "<module>"

    if is_mod:
        # Module docstring
        if code.co_consts and isinstance(code.co_consts[0], str):
            ds = code.co_consts[0]
            lines.append('"""')
            for ln in ds.split("\n"):
                lines.append(ln)
            lines.append('"""')
            lines.append("")

        # Deferred type annotation evaluation (critical for decompiled code
        # where forward references may appear before their definitions)
        lines.append('from __future__ import annotations')
        lines.append("")

        # Imports: stdlib
        added = set()
        for n in code.co_names:
            if n in _STDLIB_IMPORTS and n not in added:
                lines.append(_STDLIB_IMPORTS[n])
                added.add(n)
        if added:
            lines.append("")

        # Imports: project modules (skip self-imports)
        emitted_project_imports = set()
        for n in code.co_names:
            if n in _PROJECT_IMPORTS:
                source_mod, stmt = _PROJECT_IMPORTS[n]
                # Skip self-import: the module being decompiled cannot import from itself
                if source_mod == module_name:
                    continue
                if stmt not in emitted_project_imports:
                    lines.append(stmt)
                    emitted_project_imports.add(stmt)
        if emitted_project_imports:
            lines.append("")

        # Module-level constants
        const_names = set(n for n in code.co_names if n.isupper() and len(n) > 2)
        if "WORKSPACE_DIR" in const_names:
            lines.append('WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))')
        if "DB_PATH" in const_names:
            lines.append('DB_PATH = os.path.join(WORKSPACE_DIR, "kokertech_vault.db")')
        if const_names:
            lines.append("")

        # Logger
        if "get_logger" in code.co_names and "logger" in code.co_names:
            # Only emit logger assignment if get_logger is imported and not self-import
            log_source_mod, _ = _PROJECT_IMPORTS["get_logger"]
            if log_source_mod != module_name:
                lines.append('logger = get_logger(name="' + module_name.capitalize() + '")')
                lines.append("")

    # Functions and classes (skip __annotate__ stubs — compiler-generated, not in original source)
    for c in code.co_consts:
        if hasattr(c, "co_name") and c.co_name not in ("<module>", "__annotate__"):
            name = c.co_name
            doc = _get_docstring(c)
            is_class = _is_class(name) and name not in ("requests",)
            if is_class:
                lines.append(f"class {name}:")
                if doc:
                    lines.append('    """')
                    for ln in doc.split("\n"):
                        lines.append("    " + ln)
                    lines.append('    """')

                # Detect decorators (@property, @staticmethod, @classmethod,
                # @prop.setter, @prop.deleter) from class body bytecode.
                # Uses id(code_obj) to match because getter/setter/deleter
                # share the same function name.
                method_decorators = detect_decorators(c)

                # Extract ALL methods & nested classes from the class code object's co_consts.
                # Deduplicate to avoid emitting compiler-generated stubs alongside real method bodies.
                deduped = _deduplicate_methods(c)
                methods_found = 0
                for cc in deduped:
                    if isinstance(cc, types.CodeType) and cc.co_name not in ("<module>", "__annotate__"):
                        method_name = cc.co_name
                        # Check if this is a nested class (e.g., RequestException inside _RequestsMissingStub)
                        if _is_class(method_name):
                            lines.append(f"    class {method_name}:")
                        method_doc = _get_docstring(cc)
                        params = get_params(cc)
                        # Emit decorator if detected (matched by code object identity)
                        deco = method_decorators.get(id(cc))
                        if deco:
                            # Skip @property, @*.setter, @*.deleter if flag is set
                            if not (skip_properties and (
                                deco == '@property'
                                or deco.endswith('.setter')
                                or deco.endswith('.deleter'))):
                                lines.append(f"    {deco}")
                        if _is_class(method_name):
                            # Nested class body (decompile with indent=8)
                            if method_doc:
                                lines.append('        """')
                                for ln in method_doc.split("\n"):
                                    lines.append("        " + ln)
                                lines.append('        """')
                            try:
                                body_lines = decompile_body(cc, indent=8, is_class_body=True)
                                for bl in body_lines:
                                    lines.append(bl)
                            except Exception:
                                lines.append("        pass")
                            lines.append("")
                            methods_found += 1
                            continue

                        lines.append(f"    def {method_name}({params}):")
                        if method_doc:
                            lines.append('        """')
                            for ln in method_doc.split("\n"):
                                lines.append("        " + ln)
                            lines.append('        """')
                        # Decompile the method body
                        try:
                            body_lines = decompile_body(cc, indent=8)
                            for bl in body_lines:
                                lines.append(bl)
                        except Exception:
                            lines.append("        pass")
                        lines.append("")
                        methods_found += 1

                # Fallback: if no methods found in code object, emit empty __init__
                if methods_found == 0:
                    lines.append("    def __init__(self):")
                    lines.append("        pass")
                    lines.append("")
            else:
                params = get_params(c)
                lines.append(f"def {name}({params}):")
                if doc:
                    lines.append('    """')
                    for ln in doc.split("\n"):
                        lines.append("    " + ln)
                    lines.append('    """')
                # Decompile function body
                try:
                    body_lines = decompile_body(c, indent=4)
                    for bl in body_lines:
                        lines.append(bl)
                except Exception:
                    lines.append("    raise NotImplementedError()")
                lines.append("")

    return lines


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Decompile Python .pyc files to approximate source.")
    parser.add_argument("pyc_path", help="Path to the .pyc file")
    parser.add_argument("output_path", nargs="?", default=None,
                        help="Output .py path (default: derived from .pyc name)")
    parser.add_argument("--skip-properties", action="store_true",
                        help="Suppress @property / @*.setter / @*.deleter decorators")
    args = parser.parse_args()

    pyc = args.pyc_path
    out = args.output_path or pyc.replace(".cpython-312.pyc", ".py").replace("__pycache__/", "")
    skip_props = args.skip_properties

    code = load_code(pyc)
    module_name = derive_module_name(pyc)
    print(f"Loaded: {pyc}")
    print(f"  Module: {code.co_name}")
    print(f"  Derived module name: {module_name!r}")
    n_funcs = sum(1 for c in code.co_consts if hasattr(c, "co_name") and c.co_name not in ("<module>", "__annotate__"))
    print(f"  Functions/Classes: {n_funcs}")
    if skip_props:
        print("  Skip properties: ON")
    lines = reconstruct(code, module_name=module_name, skip_properties=skip_props)
    source = "\n".join(lines)
    with open(out, "w", encoding="utf-8") as f:
        f.write(source)
    print(f"Written: {out} ({len(source)} bytes, {len(lines)} lines)")
    import py_compile
    try:
        py_compile.compile(out, doraise=True)
        print("SYNTAX: OK")
    except py_compile.PyCompileError as e:
        print(f"SYNTAX: FAIL: {e}")


if __name__ == "__main__":
    main()
