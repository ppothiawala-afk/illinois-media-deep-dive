#!/usr/bin/env python3
"""
Offline test suite — no network, no API key. Drives the real scripts against the
clearly-labelled synthetic fixtures into a temp dir, so shipped files are never
touched.  Run:  python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures_synthetic"


def run(script, *args, expect_ok=True):
    p = subprocess.run([sys.executable, str(ROOT / script), *map(str, args)],
                       cwd=ROOT, capture_output=True, text=True)
    if expect_ok and p.returncode != 0:
        raise AssertionError(f"{script} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p


def nlines(p):
    return sum(1 for l in Path(p).read_text().splitlines() if l.strip()) if Path(p).exists() else 0


class Pipe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="il_test_")

    def ingest(self):
        return run("fetch_feeds.py", "--fixtures", FIXTURES, "--data-dir", self.tmp)

    def test_ingest_dedup_sightings_synthetic(self):
        self.ingest()
        items = [json.loads(l) for l in (Path(self.tmp)/"items_archive.jsonl").read_text().splitlines() if l.strip()]
        # the shared "budget gap" wire story (WBEZ + WTTW) collapses to one row
        budget = [i for i in items if "budget gap" in i["norm_title"]]
        self.assertEqual(len(budget), 1, "shared wire story should collapse to one archive row")
        self.assertTrue(items and all(i.get("synthetic") is True for i in items))
        # both sightings preserved
        sight = [json.loads(l) for l in (Path(self.tmp)/"item_sightings.jsonl").read_text().splitlines() if l.strip()]
        bid = budget[0]["id"]
        self.assertGreaterEqual(len([s for s in sight if s["id"]==bid]), 2)

    def test_idempotent_reingest(self):
        self.ingest(); n1 = nlines(Path(self.tmp)/"items_archive.jsonl")
        self.ingest(); n2 = nlines(Path(self.tmp)/"items_archive.jsonl")
        self.assertEqual(n1, n2)

    def test_analyze_offline_themes_and_entities(self):
        self.ingest(); run("analyze.py","--offline","--data-dir",self.tmp)
        data = json.loads((Path(self.tmp)/"items_analyzed.json").read_text())
        by = {it["title"]: it for it in data["items"]}
        budget = next(v for k,v in by.items() if "budget gap" in k.lower())
        self.assertEqual(budget["theme"], "city & state budget")
        cta = next(v for k,v in by.items() if "cta red line" in k.lower())
        self.assertEqual(cta["theme"], "transit & infrastructure")
        self.assertTrue(any(budget["entities"]), "entities extracted")

    def test_rebuild_deterministic(self):
        self.ingest()
        run("analyze.py","--offline","--data-dir",self.tmp)
        a=json.loads((Path(self.tmp)/"items_analyzed.json").read_text())["items"]
        run("analyze.py","--offline","--rebuild","--data-dir",self.tmp)
        b=json.loads((Path(self.tmp)/"items_analyzed.json").read_text())["items"]
        self.assertEqual([(x["id"],x["theme"]) for x in a],[(x["id"],x["theme"]) for x in b])

    def test_rollup_share_math_and_penetration(self):
        self.ingest(); run("analyze.py","--offline","--data-dir",self.tmp)
        run("rollup.py","--window-days","3650","--data-dir",self.tmp)
        snap=json.loads((Path(self.tmp)/"media_history.json").read_text())["snapshots"][-1]
        tot=snap["total_items"]
        for th,blk in snap["themes"].items():
            self.assertAlmostEqual(blk["share"], round(blk["volume"]/tot,4) if tot else 0.0, places=4)
        # the budget story appeared in 2 outlets -> a penetrating story
        pen=[p for p in snap["penetration"] if p["outlet_count"]>=2]
        self.assertTrue(pen, "shared story should show as penetrating")

    def test_surge_detects_spike_and_emerging(self):
        # hand-built theme_history: crime steady then spikes; immigration absent then appears
        th=Path(self.tmp)/"theme_history.jsonl"
        rows=[]
        base=["2026-07-06","2026-07-13","2026-07-20","2026-07-27","2026-08-03","2026-08-10"]
        for d in base:
            rows.append({"date":d,"theme":"public safety & crime","volume":10,"share":0.10,"outlet_breadth":3})
            rows.append({"date":d,"theme":"immigration & migrants","volume":0,"share":0.0,"outlet_breadth":0})
        rows.append({"date":"2026-08-17","theme":"public safety & crime","volume":40,"share":0.40,"outlet_breadth":6})
        rows.append({"date":"2026-08-17","theme":"immigration & migrants","volume":8,"share":0.08,"outlet_breadth":4})
        th.write_text("".join(json.dumps(r)+"\n" for r in rows))
        run("surge.py","--data-dir",self.tmp,"--min-baseline","3","--min-confident","6")
        rep=json.loads((Path(self.tmp)/"surge_report.json").read_text())
        self.assertIn("public safety & crime",[e["theme"] for e in rep["surging"]])
        self.assertIn("immigration & migrants",[e["theme"] for e in rep["emerging"]])
        crime=next(e for e in rep["surging"] if e["theme"]=="public safety & crime")
        self.assertTrue(crime["broadening"])  # breadth 3 -> 6

    def test_verify_fails_on_synthetic_in_shipped(self):
        self.ingest(); run("analyze.py","--offline","--data-dir",self.tmp)
        run("rollup.py","--window-days","3650","--data-dir",self.tmp)
        run("surge.py","--data-dir",self.tmp)
        p=run("verify_pipeline.py","--data-dir",self.tmp,expect_ok=False)
        self.assertEqual(p.returncode,1)
        rep=json.loads((Path(self.tmp)/"verification_report.json").read_text())
        s2=next(c for c in rep["checks"] if c["id"]=="S2")
        self.assertEqual(s2["status"],"FAIL")

    def test_verify_fails_on_duplicate_archive_id(self):
        self.ingest()
        arch=Path(self.tmp)/"items_archive.jsonl"
        first=arch.read_text().splitlines()[0]
        with arch.open("a") as fh: fh.write(first+"\n")
        run("analyze.py","--offline","--data-dir",self.tmp)
        p=run("verify_pipeline.py","--data-dir",self.tmp,expect_ok=False)
        rep=json.loads((Path(self.tmp)/"verification_report.json").read_text())
        d1=next(c for c in rep["checks"] if c["id"]=="D1")
        self.assertEqual(d1["status"],"FAIL"); self.assertIn("duplicate",d1["msg"])

    def test_storage_invariant_check_present(self):
        # S7 must exist and pass in a clean temp dir (nothing git-tracked there)
        self.ingest(); run("analyze.py","--offline","--data-dir",self.tmp)
        run("verify_pipeline.py","--data-dir",self.tmp,expect_ok=False)  # S2 will fail; that's fine
        rep=json.loads((Path(self.tmp)/"verification_report.json").read_text())
        s7=next(c for c in rep["checks"] if c["id"]=="S7")
        self.assertEqual(s7["status"],"PASS")


if __name__=="__main__":
    unittest.main(verbosity=2)
