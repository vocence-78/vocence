"""Tests for model fingerprinting (dedup) and the generation cache."""

import numpy as np
import pytest

from vocence.registry.fingerprint import (
    tensor_signature, fingerprint_from_tensors, cosine_similarity, FingerprintStore,
)
from vocence.pipeline.cache import GenerationCache, cached_generator


# ------------------------------------------------------------------ fingerprint
def test_signature_and_similarity_identical():
    a = {"w": np.array([1.0, 2.0, 3.0, 4.0])}
    fa = fingerprint_from_tensors(a)
    assert cosine_similarity(fa, fa) == pytest.approx(1.0)


def test_similar_models_high_dissimilar_low():
    rng = np.random.default_rng(0)
    base = rng.normal(size=(64, 64))
    tuned = base + rng.normal(scale=0.001, size=base.shape)   # near-clone
    other = rng.normal(size=(64, 64)) * 5 + 10                # very different distribution
    fb = fingerprint_from_tensors({"w": base})
    ft = fingerprint_from_tensors({"w": tuned})
    fo = fingerprint_from_tensors({"w": other})
    assert cosine_similarity(fb, ft) > 0.99      # near-clone flagged
    assert cosine_similarity(fb, fo) < cosine_similarity(fb, ft)


def test_similarity_guards():
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0  # length mismatch


def test_store_max_similarity():
    store = FingerprintStore()
    store.add("m1", fingerprint_from_tensors({"w": np.array([1.0, 2.0, 3.0])}))
    store.add("m2", fingerprint_from_tensors({"w": np.array([10.0, 20.0, 30.0])}))
    q = fingerprint_from_tensors({"w": np.array([1.0, 2.0, 3.0])})
    assert store.max_similarity(q) is not None
    assert store.max_similarity(q, exclude_ref="m1") is not None
    assert FingerprintStore().max_similarity(q) is None  # empty store


# ------------------------------------------------------------------ cache
def test_generation_cache_reuses():
    cache = GenerationCache()
    calls = {"n": 0}

    def base(text, traits):
        calls["n"] += 1
        return b"audio-" + text.encode()

    gen = cached_generator(base, digest="sha256:king", cache=cache)
    a1 = gen("hello", {}, sample_id="s1")
    a2 = gen("hello", {}, sample_id="s1")   # cache hit
    a3 = gen("world", {}, sample_id="s2")   # miss
    assert a1 == a2 == b"audio-hello"
    assert a3 == b"audio-world"
    assert calls["n"] == 2 and cache.hits == 1 and cache.misses == 2


def test_cache_evict_digest():
    cache = GenerationCache()
    gen = cached_generator(lambda t, tr: b"x", "d1", cache)
    gen("a", {}, sample_id="s1")
    gen("b", {}, sample_id="s2")
    assert cache.evict_digest("d1") == 2
    assert not cache.store
