"""Read back the four target tasks and report final state."""
import truststore; truststore.inject_into_ssl()
import os, requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
TOKEN = os.environ["CLICKUP_API_TOKEN"]

# Lookup tables (for human-readable rendering)
PILLAR = {
    "b161199b-3f7c-4f5c-a0df-b3d882259ee5": "Governance Comparison",
    "891ebe37-d6db-4949-9f09-11c5360b9b16": "Political Commentary",
    "346a2cb6-828f-4057-a56a-ea78abc809cd": "Constitutional Awareness",
    "d2c5c063-69c6-4c1a-b3c9-d8fddcfb248e": "Economic & Utility Reform",
    "6814f263-c442-4fb0-9bac-8d64fb527d89": "Filipino Empowerment",
    "1c142aa1-1160-4d0a-b50f-89da54909b58": "Business & SME Advocacy",
}
POST_TYPE = {
    "b411d4ca-43db-4cd8-b2dd-37f10e88d38a": "Static",
    "95f47825-d966-4381-a856-1e2a709e3da9": "Reactive",
    "15b2a458-ff72-49ba-8610-f6eb89df4353": "Hybrid",
    "d0f50ee1-28ee-4030-ac2f-60c5baaca0dc": "Reels",
}
PLATFORM = {
    "673cfb92-15e7-4315-9bbf-94db2baffa08": "Facebook",
    "32e72ad5-83d0-4a92-87f6-b8c8b4990a44": "Instagram",
    "225e6544-1287-44b7-a019-7f3b1fdc31e1": "Threads",
    "5bd32f20-3976-4c9e-931b-6f1d562c8c58": "Reddit",
}
ARCHETYPE = {
    "46c41af2-5383-476b-b37b-e145f47f65cc": "editorial_allegory",
    "d2521238-88ac-44c4-9248-3e4f5f5fc4b6": "ph_vs_nz_split",
    "f3b26d25-0a2a-49eb-98c5-b0a8663e8eb2": "satirical_meme",
    "a67ef797-f40c-4e9e-8c78-d67c41471dfa": "constitutional_quote",
    "207c9b7b-c2ba-4d21-919d-08665929a374": "pain_point",
}
STYLE = {
    "dfee3861-a93b-4971-b0c5-760079711a2c": "flat_editorial",
    "1e690af0-bd95-4323-9577-323a4a94d5d0": "cinematic_realistic",
    "e0fd5991-c930-4ee1-8678-72391466fc36": "hyperreal_dramatic",
    "7d88f17c-0ae2-44d1-8376-090f757b6164": "editorial_cartoon",
    "5d44f5c3-7f63-4399-b578-c54f7be7b5de": "documentary_photo",
}
FIELD_NAME_LOOKUP = {
    "b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1": ("Content Pillar", PILLAR),
    "6a3e613e-524d-4471-b9eb-8fc5451e3077": ("Post Type", POST_TYPE),
    "ef8cfddd-c950-40b8-95ca-6da001c6ac50": ("Platform", PLATFORM),
    "ff14a5f5-9124-4a92-95c0-44a33dde7ee7": ("archetype", ARCHETYPE),
    "d91c15b8-95dc-47f2-aa70-6d58effa7b01": ("style", STYLE),
}
TEXT_FIELDS = {
    "f74ba9b7-c635-48b8-a762-5ccb093eeeaa": "Image Prompt",
    "c265f867-bf8a-4be1-bf07-0777fc58cfa0": "text_in_image",
    "fa544f0a-85e2-4b55-bf41-9c121898930f": "News Hook",
    "a3cfd2e6-f963-4e39-9778-c4d488802d00": "Topic Number",
    "8a89f1c0-f964-4281-bbe8-82f2bc187ca0": "Scheduled Publish",
    "34e674b6-bfb7-4c71-80f3-35065c84f1a3": "Image URL",
    "54a8d8d0-f051-4e70-a50c-0ec526bbc1cf": "Original AI Draft",
    "f9e3e3eb-de98-406a-a716-84760d13457a": "Final Caption",
    "0f7e069d-83ab-4037-ab3a-a76720a3410d": "Threads Caption",
}

EM = chr(0x2014); EN = chr(0x2013)

for tid in ["86d30khk6", "86d30te8y", "86d30w135", "86d30tbv9"]:
    r = requests.get(
        f"https://api.clickup.com/api/v2/task/{tid}",
        headers={"Authorization": TOKEN}, timeout=30,
    )
    d = r.json()
    print("=" * 70)
    print(f"TASK {tid}")
    print("=" * 70)
    print(f"name   : {d.get('name')}")
    print(f"status : {(d.get('status') or {}).get('status')}")
    print()
    desc = d.get("description") or ""
    print(f"DESCRIPTION ({len(desc)} chars):")
    print("-" * 70)
    print(desc)
    print("-" * 70)
    print()
    em_count = desc.count(EM); en_count = desc.count(EN)
    audit = "PASS" if (em_count == 0 and en_count == 0) else "FAIL"
    print(f"em-dash / en-dash audit on description: em={em_count}, en={en_count} -> {audit}")
    print()
    print("CUSTOM FIELDS:")
    for cf in d.get("custom_fields", []):
        fid = cf.get("id")
        val = cf.get("value")
        name = cf.get("name")
        if val in (None, "", []):
            continue
        if fid in FIELD_NAME_LOOKUP:
            human_name, table = FIELD_NAME_LOOKUP[fid]
            if isinstance(val, list):
                resolved = [table.get(v, v) for v in val]
                print(f"  {human_name:<20} = {resolved}")
            else:
                print(f"  {human_name:<20} = {table.get(val, val)}")
        elif fid in TEXT_FIELDS:
            human_name = TEXT_FIELDS[fid]
            preview = str(val)[:80]
            print(f"  {human_name:<20} = {preview!r}{('...' if len(str(val)) > 80 else '')}")
        else:
            # Unknown field id - just show
            preview = str(val)[:80]
            print(f"  {name:<20} = {preview!r}")
    print()
    print(f"attachments: {len(d.get('attachments', []))}")
    for a in d.get("attachments", [])[:3]:
        print(f"  - {a.get('title')} ({a.get('extension')})")
    print()
