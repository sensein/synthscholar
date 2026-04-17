#!/usr/bin/env python3
"""
reconstruct.py — Nine provenance verification tests for an SLR instance.

Usage: python reconstruct.py [example.yaml]

Exit 0 if all 9 tests pass. Exit 1 on any failure.
"""

import sys
import json
import copy
import hashlib
import yaml
from pathlib import Path


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── helpers ───────────────────────────────────────────────────────────────

def get_included_source(data: dict, index: int = 0) -> dict:
    return data["included_sources"][index]


def get_charting_history(source: dict) -> list[dict]:
    return source.get("charting_record_history", [])


def get_current_charting(source: dict) -> dict:
    return source["charting_record"]


def get_review_event(charting_record: dict) -> dict | None:
    """Find first ReviewEvent in the was_generated_by activity's review_events."""
    activity = charting_record.get("was_generated_by", {})
    events = activity.get("review_events", [])
    return events[0] if events else None


def get_artifact_index(data: dict) -> dict[str, dict]:
    """Build content_hash -> artifact dict."""
    artifacts = data.get("artifact_store", {}).get("artifacts", [])
    return {a["content_hash"]: a for a in artifacts}


def dot_navigate(obj: dict, path: str):
    """Navigate a dot-delimited path in a nested dict."""
    parts = path.split(".")
    cur = obj
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def dot_set(obj: dict, path: str, value) -> None:
    """Set a value at a dot-delimited path in a nested dict."""
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


# ── tests ─────────────────────────────────────────────────────────────────

def test_1_v1_recoverable(data: dict) -> str:
    """v1 is in charting_record_history; its pre-revision field value is present."""
    source = get_included_source(data)
    history = get_charting_history(source)
    v1_records = [r for r in history if r.get("version_number") == 1]
    assert v1_records, "No v1 record found in charting_record_history"
    v1 = v1_records[0]
    country = dot_navigate(v1, "section_a_publication.country_region")
    assert country, "v1.section_a_publication.country_region is empty"
    return f"v1 found; country_region = '{country}'"


def test_2_protocol_recoverable(data: dict) -> str:
    """Pre-workflow session has user_inputs and decisions_locked."""
    sessions = data.get("pre_workflow_sessions", [])
    assert sessions, "No pre_workflow_sessions found"
    session = sessions[0]
    assert session.get("user_inputs"), "pre_workflow_sessions[0].user_inputs is empty"
    assert session.get("decisions_locked"), "pre_workflow_sessions[0].decisions_locked is empty"
    n_inputs = len(session["user_inputs"])
    n_decisions = len(session["decisions_locked"])
    return f"{n_inputs} user_inputs, {n_decisions} decisions_locked"


def test_3_diff_recoverable(data: dict) -> str:
    """ReviewEvent fields_changed matches keys in original_values and revised_values."""
    source = get_included_source(data)
    current = get_current_charting(source)
    event = get_review_event(current)
    assert event, "No ReviewEvent found in v2 activity"

    fields_changed = event.get("fields_changed", [])
    assert fields_changed, "fields_changed is empty"

    orig = json.loads(event["original_values"])
    revised = json.loads(event["revised_values"])

    assert set(fields_changed) == set(orig.keys()), \
        f"fields_changed {fields_changed} != original_values keys {list(orig.keys())}"
    assert set(fields_changed) == set(revised.keys()), \
        f"fields_changed {fields_changed} != revised_values keys {list(revised.keys())}"

    return f"diff verified: {fields_changed}"


def test_4_forward_trace(data: dict) -> str:
    """UserInput.influences maps to at least one downstream record."""
    record_to_inputs: dict[str, list[str]] = {}
    for session in data.get("pre_workflow_sessions", []):
        for ui in session.get("user_inputs", []):
            for influenced_id in ui.get("influences", []):
                record_to_inputs.setdefault(influenced_id, []).append(ui["input_id"])
    assert record_to_inputs, "No influence mappings found"
    return f"{len(record_to_inputs)} records influenced by user inputs"


def test_5_replay_spec_complete(data: dict) -> str:
    """v1 model invocation has the minimum fields for deterministic replay."""
    source = get_included_source(data)
    history = get_charting_history(source)
    v1 = next(r for r in history if r.get("version_number") == 1)

    activity = v1.get("was_generated_by", {})
    invocations = activity.get("model_invocations", [])
    assert invocations, "No model_invocations on v1 activity"

    inv = invocations[0]
    config = inv.get("configuration", {})

    required = ["model_name", "model_version", "temperature", "seed", "max_tokens"]
    missing = [f for f in required if config.get(f) is None]
    assert not missing, f"Missing replay fields: {missing}"

    return (f"model={config['model_name']} v={config['model_version']} "
            f"T={config['temperature']} seed={config['seed']}")


def test_6_hash_resolution(data: dict) -> str:
    """Every prompt_hash and response_hash referenced in model invocations exists in artifact_store."""
    artifact_index = get_artifact_index(data)

    referenced = set()
    for source in data.get("included_sources", []):
        records = [source.get("charting_record")] + source.get("charting_record_history", [])
        for record in records:
            if not record:
                continue
            activity = record.get("was_generated_by", {})
            for inv in activity.get("model_invocations", []):
                prompt = inv.get("prompt", {})
                if h := prompt.get("prompt_hash"):
                    referenced.add(h)
                if h := inv.get("response_hash"):
                    referenced.add(h)
            for tool in activity.get("tool_invocations", []):
                if h := tool.get("result_hash"):
                    referenced.add(h)

    missing = referenced - set(artifact_index.keys())
    assert not missing, f"Unresolved hashes: {[h[:16] + '...' for h in missing]}"

    inline_count = sum(1 for h in referenced if artifact_index[h].get("content_text"))
    external_count = len(referenced) - inline_count
    return f"{len(referenced)} hashes resolved ({inline_count} inline, {external_count} external)"


def test_7_integrity_check(data: dict) -> str:
    """Every StoredArtifact with content_text has content_hash == SHA-256(content_text)."""
    artifacts = data.get("artifact_store", {}).get("artifacts", [])
    checked = 0
    for art in artifacts:
        content = art.get("content_text")
        if content:
            expected = sha256(content)
            actual = art["content_hash"]
            assert expected == actual, \
                (f"Hash mismatch for {art.get('artifact_kind')}: "
                 f"expected {expected[:16]}... got {actual[:16]}...")
            checked += 1
    assert checked > 0, "No artifacts with content_text to verify"
    return f"{checked} artifact hashes verified"


def test_8_tamper_detection(data: dict) -> str:
    """Appending text to content_text produces a different hash."""
    artifacts = data.get("artifact_store", {}).get("artifacts", [])
    inline = [a for a in artifacts if a.get("content_text")]
    assert inline, "No inline artifacts to test"

    original = copy.deepcopy(inline[0])
    tampered_text = original["content_text"] + " [TAMPERED]"
    tampered_hash = sha256(tampered_text)

    assert tampered_hash != original["content_hash"], \
        "Tamper detection failed: hash unchanged after content modification"
    return f"tamper detected: {original['content_hash'][:16]}... != {tampered_hash[:16]}..."


def test_9_round_trip_replay(data: dict) -> str:
    """Apply revised_values to v1 content; result matches v2 for changed fields."""
    source = get_included_source(data)
    history = get_charting_history(source)
    v1 = next(r for r in history if r.get("version_number") == 1)
    v2 = get_current_charting(source)

    event = get_review_event(v2)
    assert event, "No ReviewEvent for replay"

    fields_changed = event["fields_changed"]
    revised = json.loads(event["revised_values"])

    # Deep copy v1 content, apply revisions
    replayed = copy.deepcopy(v1)
    for field_path, new_value in revised.items():
        dot_set(replayed, field_path, new_value)

    # Check that replayed values match v2 for each changed field
    for field_path in fields_changed:
        replayed_val = dot_navigate(replayed, field_path)
        v2_val = dot_navigate(v2, field_path)
        assert replayed_val == v2_val, \
            f"Replay mismatch at {field_path}: replayed={replayed_val!r} vs v2={v2_val!r}"

    return f"replay correct for fields: {fields_changed}"


# ── runner ────────────────────────────────────────────────────────────────

TESTS = [
    ("Test 1 — v1 recoverable",        test_1_v1_recoverable),
    ("Test 2 — Protocol recoverable",  test_2_protocol_recoverable),
    ("Test 3 — Diff recoverable",      test_3_diff_recoverable),
    ("Test 4 — Forward-trace works",   test_4_forward_trace),
    ("Test 5 — Replay spec complete",  test_5_replay_spec_complete),
    ("Test 6 — Hash resolution works", test_6_hash_resolution),
    ("Test 7 — Integrity check",       test_7_integrity_check),
    ("Test 8 — Tamper detection",      test_8_tamper_detection),
    ("Test 9 — Round-trip replay",     test_9_round_trip_replay),
]


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "example.yaml"
    print(f"Loading {path}\n")
    data = load(path)

    passed = 0
    failed = 0
    for name, fn in TESTS:
        try:
            detail = fn(data)
            print(f"  PASS  {name}")
            if detail:
                print(f"        {detail}")
            passed += 1
        except (AssertionError, KeyError, TypeError, StopIteration) as e:
            print(f"  FAIL  {name}")
            print(f"        {e}")
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"  {passed}/{len(TESTS)} tests passed")
    if failed:
        print(f"  {failed} FAILURES — see above")
        sys.exit(1)
    else:
        print("  ALL PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
