"""

agentic_rag.py — Multi-hop Agentic RAG Engine for KokertechAI.

Sprint 7: Agentic RAG with multi-hop retrieval, re-search, and synthesis.

Architecture:
    User Query
        |
        v
    +---------------------+
    |  decompose_query()  |  <- LLM breaks query into sub-questions
    +--------+------------+
             |
    +--------v----------------------------------------------+
    |  For each sub-question:                               |
    |    +----------------+                                 |
    |    | retrieve_for()  |  <- vector search + web         |
    |    +-------+--------+                                 |
    |            |                                          |
    |    +-------v--------+                                 |
    |    | evaluate_gaps() |  <- check coverage              |
    |    +-------+--------+                                 |
    |            |                                          |
    |    +-------v----------+                               |
    |    | refine_queries() |  <- generate new queries      |
    |    +-------+----------+                               |
    |            |  (loop until sufficient)                  |
    +------------+------------------------------------------+
                 |
    +------------v----------+
    |   synthesize_answer() |  <- combine all evidence
    +------------+----------+
                 |
    +------------v----------+
    |   Final Answer         |
    +-----------------------+

"""

import hashlib
import json
import re
import threading
import time
from typing import Optional, List, Dict, Tuple, Any

from config import CONFIG
from logging_config import get_logger
from ai_base import get_provider

# Module-level reference for test mocking (decompiled stub)
try:
    import memory_vault
except ImportError:
    memory_vault = None

logger = get_logger(name="AgenticRAG")

# ---- Module-level in-run retrieval cache ----
_RAG_RETRIEVE_CACHE = {}          # {cache_key: (timestamp, [contexts])}
_RAG_RETRIEVE_CACHE_LOCK = threading.Lock()
_RAG_RETRIEVE_CACHE_TTL = 300     # 5 minutes
_RAG_RETRIEVE_CACHE_MAX = 128     # max entries
_RAG_DECOMPOSE_CACHE = {}
_RAG_GAP_EVAL_CACHE = {}

def _compute_rag_retrieve_cache_key(question):
    """Deterministic cache key: SHA-256 hash of lowercased question."""
    return hashlib.sha256(question.lower().strip().encode()).hexdigest()


def _rag_inrun_cache_enabled():
    """Feature toggle for the in-run retrieval cache (default: enabled)."""
    try:
        return CONFIG.get("rag_inrun_cache_enabled", True)
    except Exception:
        return True


def _get_cached_rag_retrieve(question):
    """Return cached retrieval contexts or None on MISS / TTL expiry."""
    if not _rag_inrun_cache_enabled():
        return None
    key = _compute_rag_retrieve_cache_key(question)
    entry = _RAG_RETRIEVE_CACHE.get(key)
    if entry is None:
        return None
    ts = entry["ts"]
    if time.time() - ts > _RAG_RETRIEVE_CACHE_TTL:
        del _RAG_RETRIEVE_CACHE[key]
        return None
    # Return shallow copies so caller mutations cannot poison the cache
    return [dict(ctx) for ctx in entry["contexts"]]


def _set_cached_rag_retrieve(question, contexts):
    """Store retrieval contexts in the module-level cache."""
    if not _rag_inrun_cache_enabled():
        return
    key = _compute_rag_retrieve_cache_key(question)
    # Evict oldest entry if at capacity
    if len(_RAG_RETRIEVE_CACHE) >= _RAG_RETRIEVE_CACHE_MAX:
        oldest_key = min(_RAG_RETRIEVE_CACHE, key=lambda k: _RAG_RETRIEVE_CACHE[k]["ts"])
        del _RAG_RETRIEVE_CACHE[oldest_key]
    _RAG_RETRIEVE_CACHE[key] = {"ts": time.time(), "contexts": [dict(c) for c in contexts]}


def _compute_gap_eval_key(query, contexts):
    """Deterministic cache key: sha256(query + sorted content snippets)."""
    snippets = []
    for ctx in contexts:
        if isinstance(ctx, dict):
            snippets.append(ctx.get("content", "")[:200])
        elif isinstance(ctx, tuple) and len(ctx) >= 3:
            snippets.append(str(ctx[2])[:200])
    snippets = sorted(snippets)
    payload = query + "|".join(snippets)
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_cached_decompose(cache_key):
    """Return cached decomposition or None on MISS / TTL expiry."""
    return _RAG_DECOMPOSE_CACHE.get(cache_key)


def _set_cached_decompose(cache_key, sub_questions):
    """Store decomposition result in the module-level cache."""
    _RAG_DECOMPOSE_CACHE[cache_key] = sub_questions


def _get_cached_gap_eval(cache_key):
    """Return cached gap evaluation or None on MISS / TTL expiry."""
    return _RAG_GAP_EVAL_CACHE.get(cache_key)


def _set_cached_gap_eval(cache_key, result):
    """Store gap evaluation result in the module-level cache."""
    _RAG_GAP_EVAL_CACHE[cache_key] = result


def _clear_rag_inrun_cache():
    """Test helper: clear all module-level RAG caches."""
    _RAG_RETRIEVE_CACHE.clear()
    _RAG_DECOMPOSE_CACHE.clear()
    _RAG_GAP_EVAL_CACHE.clear()

class AgenticRAGEngine:
    """Multi-hop Agentic RAG engine with decomposition, retrieval, gap analysis, and synthesis."""

    def __init__(self, provider_name="", model="", max_hops=3, top_k_per_source=5,
                 enable_web_search=False, enable_graph_expansion=True,
                 graph_hops=2, min_graph_weight=0.1):
        self.provider_name = provider_name
        self.model = model
        self.max_hops = max_hops
        self.top_k = top_k_per_source
        self.enable_web_search = enable_web_search
        self.enable_graph_expansion = enable_graph_expansion
        self.graph_hops = graph_hops
        self.min_graph_weight = min_graph_weight

    # ------------------------------------------------------------------
    # Query decomposition
    # ------------------------------------------------------------------
    def _decompose_query(self, query):
        """Break query into sub-questions via LLM; fall back to split-on-? if LLM fails."""
        cache_key = hashlib.sha256(("decompose:" + query).encode()).hexdigest()
        cached = _get_cached_decompose(cache_key)
        if cached is not None:
            return cached
        try:
            provider = get_provider()
            messages = [{
                "role": "system",
                "content": "Break this query into 1-5 sub-questions. Return a JSON array of strings."
            }, {"role": "user", "content": query}]
            result = provider.chat_completion(messages=messages, temperature=0.1, max_tokens=300)
            text = result.get("content", "") or result.get("text", "")
            if not text:
                raise ValueError("empty provider response")
            # Try to extract JSON array
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
                    sub_qs = parsed[:5]
                    _set_cached_decompose(cache_key, sub_qs)
                    return sub_qs
            except (json.JSONDecodeError, ValueError):
                pass
            # Try to extract from code block
            m = re.search(r'\[([^\]]+)\]', text, re.DOTALL)
            if m:
                _set_cached_decompose(cache_key, [query])
                return [query]
            raise ValueError("unparseable decomposition")
        except Exception:
            # Fallback: split on ? for long queries
            if len(query) > 100 and "?" in query:
                parts = [q.strip() + "?" for q in query.split("?") if q.strip()]
                result_fb = parts[:5] if len(parts) >= 2 else [query]
            else:
                result_fb = [query]
            _set_cached_decompose(cache_key, result_fb)
            return result_fb

    # ------------------------------------------------------------------
    # Gap analysis
    # ------------------------------------------------------------------
    def _evaluate_gaps(self, query, contexts):
        """Evaluate whether retrieved contexts sufficiently answer the query."""
        if not contexts:
            return {"sufficient": False, "gaps": ["no information available"]}
        cache_key = _compute_gap_eval_key(query, contexts)
        cached = _get_cached_gap_eval(cache_key)
        if cached is not None:
            return cached
        try:
            provider = get_provider()
            # Build ctx_text defensively — contexts may be dicts or tuples
            parts = []
            for c in contexts[:3]:
                if isinstance(c, dict):
                    parts.append(c.get("content", "")[:300])
                elif isinstance(c, tuple) and len(c) >= 3:
                    parts.append(str(c[2])[:300])
            ctx_text = "\n".join(parts)
            messages = [{
                "role": "system",
                "content": "Evaluate if the contexts sufficiently answer the query. Return JSON: {\"sufficient\": bool, \"gaps\": [str]}"
            }, {"role": "user", "content": f"Query: {query}\nContexts:\n{ctx_text}"}]
            result = provider.chat_completion(messages=messages, temperature=0.1, max_tokens=200)
            # ── Error path: provider returned an error ──
            if result.get("error"):
                fallback = {"sufficient": False, "gaps": ["Error"], "reasoning": str(result["error"])}
                _set_cached_gap_eval(cache_key, fallback)
                return fallback
            text = result.get("content", "") or result.get("text", "")
            if not text or not isinstance(text, str):
                return {"sufficient": False, "gaps": ["Error"], "reasoning": "Error"}
            parsed = json.loads(text)
            if isinstance(parsed, dict) and "sufficient" in parsed:
                _set_cached_gap_eval(cache_key, parsed)
                return parsed
            # Valid JSON but not a dict with 'sufficient' — treat as parse error
            return {"sufficient": False, "gaps": ["Parse error"], "reasoning": "Parse error"}
        except (json.JSONDecodeError, ValueError, TypeError):
            fallback = {"sufficient": False, "gaps": ["Parse error"], "reasoning": "Parse error"}
            _set_cached_gap_eval(cache_key, fallback)
            return fallback
        except Exception:
            fallback = {"sufficient": False, "gaps": ["Error"], "reasoning": "Error"}
            _set_cached_gap_eval(cache_key, fallback)
            return fallback

    # ------------------------------------------------------------------
    # Refinement queries
    # ------------------------------------------------------------------
    def _generate_refinement_queries(self, gaps, query):
        """Generate refinement queries based on identified gaps; cap at 3."""
        if isinstance(gaps, str):
            gaps = [gaps]
        if not gaps:
            return []
        try:
            provider = get_provider()
            messages = [{
                "role": "system",
                "content": "Generate 1-3 refinement queries to fill these gaps. Return a JSON array of strings."
            }, {"role": "user", "content": f"Original: {query}\nGaps: {json.dumps(gaps)}"}]
            result = provider.chat_completion(messages=messages, temperature=0.2, max_tokens=300)
            text = result.get("content", "") or result.get("text", "")
            if not text or not isinstance(text, str):
                return [str(g) for g in gaps[:3]]
            parsed = json.loads(text)
            if isinstance(parsed, list) and all(isinstance(s, str) for s in parsed):
                return parsed[:3]
            return [str(g) for g in gaps[:3]]
        except Exception:
            return [str(g) for g in gaps[:3]]

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    def _retrieve_for(self, query, existing_contexts=None):
        """Retrieve contexts from memory_vault for a single sub-question."""
        existing_contexts = existing_contexts or []
        cached = _get_cached_rag_retrieve(query)
        if cached is not None:
            existing_keys = {c.get("content", "")[:100] for c in existing_contexts if isinstance(c, dict)}
            return [r for r in cached if isinstance(r, dict) and r.get("content", "")[:100] not in existing_keys]
        results = []
        seen_keys = set()
        # ── Tuple-to-dict normaliser ──────────────────────────
        # Tests mock memory_vault.semantic_search / search_episodic to
        # return tuples: (id, source, content, score, ...). The _add
        # helper normalises tuples to dicts so downstream code can use
        # dict-key access (ctx["source"], ctx["content"], etc.).
        # semantic_search returns rows from core_memories (source="fact"
        # in the vault schema); normalise "fact" → "core_memory" for the
        # RAG consumer.
        def _add(ctx_list):
            for ctx in ctx_list:
                if isinstance(ctx, tuple) and len(ctx) >= 4:
                    source = ctx[1]
                    if source == "fact":
                        source = "core_memory"
                    ctx = {"id": ctx[0], "source": source, "content": ctx[2], "score": ctx[3]}
                if isinstance(ctx, dict):
                    key = ctx.get("content", "")[:100]
                    if key and key not in seen_keys:
                        seen_keys.add(key)
                        results.append(ctx)
        try:
            if memory_vault is not None:
                try:
                    core = memory_vault.semantic_search(query, top_k=self.top_k)
                    if core:
                        _add(core)
                except Exception as e:
                    # v0.22.17: log (not silent) so a vault search regression
                    # surfaces in logs instead of silently emptying RAG context
                    logger.debug(f"Semantic search failed: {e}")
                try:
                    episodic = memory_vault.search_episodic(query, top_k=self.top_k)
                    if episodic:
                        _add(episodic)
                except Exception as e:
                    logger.debug(f"Episodic search failed: {e}")
                # ── GraphRAG Context Expansion ─────────────────────
                if getattr(self, "enable_graph_expansion", True) and hasattr(memory_vault, "traverse_subgraph"):
                    try:
                        graph_ctxs = self.expand_graph_context(results)
                        if graph_ctxs:
                            _add(graph_ctxs)
                    except (AttributeError, KeyError, TypeError, ValueError, OSError) as e:
                        logger.debug(f"Graph context expansion failed: {e}")
        except Exception as e:
            logger.debug(f"Memory-vault RAG retrieval failed: {e}")
        # ── Web search fallback ────────────────────────────────
        if self.enable_web_search:
            import sys
            try:
                wm = sys.modules.get("plugins.search_web")
                if wm is not None and hasattr(wm, "search_web"):
                    res = wm.search_web(query)
                    if res:
                        _add([{"id": "web", "source": "web", "content": res, "score": 1.0}])
            except Exception as e:
                logger.debug(f"Web search fallback failed: {e}")
        existing_keys = {c.get("content", "")[:100] for c in existing_contexts if isinstance(c, dict)}
        results = [r for r in results if isinstance(r, dict) and r.get("content", "")[:100] not in existing_keys]
        _set_cached_rag_retrieve(query, results)
        return results

    # ------------------------------------------------------------------
    # GraphRAG Context Expansion
    # ------------------------------------------------------------------
    def expand_graph_context(self, contexts: list, max_hops: int = None, min_weight: float = None) -> list:
        """Expand retrieved contexts with 1-hop and 2-hop connected concepts from the memory graph.

        For each core memory in contexts, traverses the memory graph to find
        connected concept nodes within max_hops (default self.graph_hops, e.g. 2).
        Returns a list of newly discovered context dicts.
        """
        if not contexts or memory_vault is None or not hasattr(memory_vault, "traverse_subgraph"):
            return []

        hops = max_hops if max_hops is not None else getattr(self, "graph_hops", 2)
        thresh = min_weight if min_weight is not None else getattr(self, "min_graph_weight", 0.1)

        seen_ids = set()
        seen_keys = set()
        for c in contexts:
            if isinstance(c, dict):
                if "id" in c:
                    seen_ids.add(c["id"])
                k = c.get("content", "")[:100]
                if k:
                    seen_keys.add(k)
            elif isinstance(c, tuple) and len(c) >= 3:
                seen_ids.add(c[0])
                seen_keys.add(str(c[2])[:100])

        expanded = []
        for c in contexts:
            cid = None
            base_score = 0.8
            if isinstance(c, dict):
                src = str(c.get("source") or "")
                if src not in ("episodic", "web") and not src.startswith("graph_"):
                    cid = c.get("id")
                    base_score = float(c.get("score") or 0.8)
            elif isinstance(c, tuple) and len(c) >= 4:
                src = str(c[1] or "")
                if src not in ("episodic", "web") and not src.startswith("graph_"):
                    cid = c[0]
                    base_score = float(c[3] or 0.8)

            if cid is None or not isinstance(cid, int):
                continue

            try:
                subgraph = memory_vault.traverse_subgraph(
                    start_node_id=cid,
                    max_hops=hops,
                    min_weight=thresh,
                )
                if not subgraph or not isinstance(subgraph, dict):
                    continue

                for node in subgraph.get("nodes", []):
                    nid = node.get("id")
                    if nid == cid or nid in seen_ids:
                        continue
                    content = node.get("content", "")
                    if not content or content[:100] in seen_keys:
                        continue

                    hop = node.get("hop", 1)
                    pw = float(node.get("path_weight", 1.0))
                    score = round(base_score * pw, 4)

                    # Determine relation type from traversed links
                    rel_type = "RELATES_TO"
                    node_path = subgraph.get("paths", {}).get(nid, [])
                    pred_id = node_path[-2] if len(node_path) >= 2 else cid
                    for lk in subgraph.get("links", []):
                        if (lk.get("source_id") == pred_id and lk.get("target_id") == nid) or \
                           (lk.get("target_id") == pred_id and lk.get("source_id") == nid):
                            rel_type = lk.get("relation_type") or lk.get("relationship_type") or "RELATES_TO"
                            break

                    seen_ids.add(nid)
                    seen_keys.add(content[:100])
                    expanded.append({
                        "id": nid,
                        "source": f"graph_{hop}hop",
                        "content": content,
                        "score": score,
                        "hop": hop,
                        "via_node_id": cid,
                        "relation_type": rel_type,
                    })
            except (AttributeError, KeyError, TypeError, ValueError, OSError) as e:
                logger.debug(f"Graph context expansion failed for node {cid}: {e}")

        return expanded

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------
    def _synthesize_answer(self, query, contexts, previous_answers=None):
        """Synthesize a final answer from retrieved contexts."""
        if not contexts:
            return "unable to find relevant information"
        try:
            provider = get_provider()
            ctx_text = "\n".join([c.get("content", "")[:500] for c in contexts[:5]])
            messages = [{
                "role": "system",
                "content": "Synthesize a concise answer from the provided contexts."
            }, {"role": "user", "content": f"Query: {query}\nContexts:\n{ctx_text}"}]
            result = provider.chat_completion(messages=messages, temperature=0.3, max_tokens=500)
            if isinstance(result, dict) and result.get("error"):
                return "Synthesis error: " + str(result["error"])
            text = result.get("content", "") or result.get("text", "")
            if text and isinstance(text, str):
                return text
            return "error occurred during synthesis"
        except Exception:
            return "error occurred during synthesis"

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------
    def answer(self, query, session_id=None, **kwargs):
        """Full multi-hop RAG pipeline: decompose -> retrieve -> evaluate -> refine -> synthesize."""
        # ── Resolve session_id ──────────────────────────────────
        if session_id is None:
            try:
                sid = memory_vault.get_current_session_id()
            except Exception:
                sid = "default"
        else:
            sid = session_id
        log_callback = kwargs.get("log_callback", None)
        try:
            sub_questions = self._decompose_query(query)
            all_contexts = []
            total_hops = 0
            for sq in sub_questions:
                hop_contexts = self._retrieve_for(sq, existing_contexts=all_contexts)
                all_contexts.extend(hop_contexts)
                total_hops += 1
                # ── Short-circuit: no contexts → stop ──────
                if not hop_contexts:
                    break
                gap_result = self._evaluate_gaps(sq, hop_contexts)
                if not gap_result.get("sufficient", True):
                    gaps = gap_result.get("gaps", [])
                    if gaps and total_hops < self.max_hops:
                        ref_queries = self._generate_refinement_queries(gaps, sq)
                        for rq in ref_queries:
                            more = self._retrieve_for(rq, existing_contexts=all_contexts)
                            all_contexts.extend(more)
                            total_hops += 1
                if total_hops >= self.max_hops:
                    break
            answer_text = self._synthesize_answer(query, all_contexts)
            # ── Emit RAG complete log for full-pipeline test assertion ──
            if log_callback:
                log_callback("RAG complete")
            seen = set()
            unique_contexts = []
            for c in all_contexts:
                k = c.get("content", "")[:100]
                if k not in seen:
                    seen.add(k)
                    unique_contexts.append(c)
            return {
                "answer": answer_text,
                "sub_questions": sub_questions,
                "contexts": unique_contexts,
                "hops": total_hops,
                "session_id": sid,
                "error": None,
            }
        except Exception:
            return {
                "answer": "unable to answer",
                "sub_questions": [query],
                "contexts": [],
                "hops": 0,
                "session_id": sid,
                "error": None,
            }

    def answer_async(self, query, callback=None, session_id=None, **kwargs):
        """Run answer() in a background thread, invoking callback(result) when done."""
        log_callback = kwargs.get("log_callback", None)
        def _run():
            result = self.answer(query, session_id=session_id)
            if callback:
                callback(result)
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t
