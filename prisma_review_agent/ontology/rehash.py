#!/usr/bin/env python3
"""
rehash.py — Recompute SHA-256 hashes for all StoredArtifacts and update all references.

Usage: python rehash.py [example.yaml]  (defaults to example.yaml in same dir)
"""

import sys
import hashlib
import yaml
from pathlib import Path


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect_artifacts(data: dict) -> list[dict]:
    """Return the list of StoredArtifact dicts from data['artifact_store']['artifacts']."""
    store = data.get("artifact_store", {})
    return store.get("artifacts", [])


def build_content_to_hash(artifacts: list[dict]) -> dict[str, str]:
    """Build a map of content_text -> correct SHA-256 hash for all artifacts with content_text."""
    result = {}
    for artifact in artifacts:
        content = artifact.get("content_text")
        if content:
            result[content] = compute_hash(content)
    return result


def update_artifact_hashes(artifacts: list[dict], content_to_hash: dict[str, str]) -> int:
    """Update content_hash on each artifact in-place. Returns number of changes."""
    changes = 0
    for artifact in artifacts:
        content = artifact.get("content_text")
        if content and content in content_to_hash:
            new_hash = content_to_hash[content]
            if artifact.get("content_hash") != new_hash:
                artifact["content_hash"] = new_hash
                changes += 1
    return changes


def fix_invocation_hashes(data: dict, content_to_hash: dict[str, str]) -> None:
    """Walk all model/tool invocations and fix prompt_hash, response_hash, result_hash.

    Matching strategy:
    - For a Prompt: concatenate system_prompt + "\\n\\n" + user_prompt and look up in content_to_hash.
    - For ModelInvocation: response_text is stored verbatim as artifact content_text.
    - For ToolInvocation: result_text is stored verbatim as artifact content_text.
    """
    def fix_activity(activity: dict) -> None:
        if not isinstance(activity, dict):
            return
        for inv in activity.get("model_invocations", []):
            if not isinstance(inv, dict):
                continue
            # Fix response_hash
            resp_text = inv.get("response_text")
            if resp_text and resp_text in content_to_hash:
                inv["response_hash"] = content_to_hash[resp_text]
            # Fix prompt_hash
            prompt = inv.get("prompt", {})
            if isinstance(prompt, dict):
                sp = prompt.get("system_prompt", "") or ""
                up = prompt.get("user_prompt", "") or ""
                full_prompt = sp + "\n\n" + up
                if full_prompt in content_to_hash:
                    prompt["prompt_hash"] = content_to_hash[full_prompt]
        for tool in activity.get("tool_invocations", []):
            if not isinstance(tool, dict):
                continue
            res_text = tool.get("result_text")
            if res_text and res_text in content_to_hash:
                tool["result_hash"] = content_to_hash[res_text]

    for source in data.get("included_sources", []):
        if not isinstance(source, dict):
            continue
        cr = source.get("charting_record")
        if isinstance(cr, dict):
            fix_activity(cr.get("was_generated_by", {}))
        for hist_cr in source.get("charting_record_history", []):
            if isinstance(hist_cr, dict):
                fix_activity(hist_cr.get("was_generated_by", {}))


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "example.yaml"

    print(f"Loading {path}")
    raw_text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text)

    artifacts = collect_artifacts(data)
    print(f"Found {len(artifacts)} StoredArtifacts")

    # Step 1: Build correct hash for each unique content_text
    content_to_hash = build_content_to_hash(artifacts)
    print(f"Unique content texts: {len(content_to_hash)}")

    # Step 2: Update artifact content_hash fields
    changes = update_artifact_hashes(artifacts, content_to_hash)
    print(f"Artifact hash updates: {changes}")

    # Step 3: Fix all invocation hash references
    fix_invocation_hashes(data, content_to_hash)
    print("Invocation hash references updated.")

    # Step 4: Dump updated YAML
    updated_text = yaml.dump(data, allow_unicode=True, sort_keys=False, width=120)
    path.write_text(updated_text, encoding="utf-8")
    print(f"Written: {path}")

    # Step 5: Verify
    verify_data = yaml.safe_load(updated_text)
    verify_artifacts = collect_artifacts(verify_data)
    errors = 0
    for art in verify_artifacts:
        content = art.get("content_text")
        if content:
            expected = compute_hash(content)
            actual = art.get("content_hash", "")
            if expected != actual:
                print(f"  MISMATCH: {art.get('artifact_kind')} "
                      f"expected {expected[:16]}... got {actual[:16]}...")
                errors += 1

    # Also verify invocation hash references
    print("\nInvocation hash spot checks:")
    for source in verify_data.get("included_sources", []):
        for record_label, record in [("v2", source.get("charting_record"))] + \
                                    [("v1", h) for h in source.get("charting_record_history", [])]:
            if not isinstance(record, dict):
                continue
            activity = record.get("was_generated_by", {})
            for inv in activity.get("model_invocations", []):
                ph = inv.get("prompt", {}).get("prompt_hash", "")
                rh = inv.get("response_hash", "")
                placeholder = "a" * 64
                if ph == placeholder:
                    print(f"  WARNING [{record_label}] prompt_hash still placeholder")
                    errors += 1
                else:
                    print(f"  OK [{record_label}] prompt_hash: {ph[:16]}...")
                if rh == placeholder:
                    print(f"  WARNING [{record_label}] response_hash still placeholder")
                    errors += 1
                else:
                    print(f"  OK [{record_label}] response_hash: {rh[:16]}...")
            for tool in activity.get("tool_invocations", []):
                rh = tool.get("result_hash", "")
                if rh == "a" * 64:
                    print(f"  WARNING [{record_label}] result_hash still placeholder")
                    errors += 1
                else:
                    print(f"  OK [{record_label}] result_hash: {rh[:16]}...")

    print()
    if errors == 0:
        print("All hashes verified OK.")
    else:
        print(f"{errors} hash issues found!")
        sys.exit(1)


if __name__ == "__main__":
    main()
