from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.classifier import classify, classify_signal_type, event_from_item, is_meaningful
from app.config import ROOT, sources_config, topics_config
from app.database import Database
from app.http import Response
from app.models import RawItem
from app.parsing import article_text, parse_date, parse_feed, parse_rbi_notifications, parse_wordpress_posts
from app.pipeline import Pipeline
from app.reporting import render_report
from app.quality import publication_issues


NOW = datetime(2026, 8, 30, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.feed = (ROOT / "tests" / "fixtures" / "feed.xml").read_bytes()

    def get(self, url):
        if self.fail:
            raise TimeoutError("simulated timeout")
        if url.endswith("feed.xml"):
            return Response(url, 200, "application/rss+xml", self.feed)
        body = b"""<html><body><main><p>RBI issued draft directions on digital lending disclosures.
        Banks and NBFCs would give borrowers a standard key facts statement before executing a loan.
        The proposed framework covers disclosure format, annual percentage rates, grievance contacts,
        cooling-off periods and the responsibilities of regulated entities that use lending service providers.
        It would also require regulated entities to review their contracts and digital customer journeys.
        The proposed disclosure would identify the lender, explain all borrower charges, record recovery-agent
        arrangements and provide a durable copy before loan execution. Regulated entities would remain
        responsible for outsourced service providers and for handling complaints raised through digital channels.
        Public comments are invited by 15 September 2026. The directions are not yet in force, and the
        regulator has not stated a commencement date for any final instrument.</p></main></body></html>"""
        return Response(url, 200, "text/html", body)


class SystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        shutil.copy(ROOT / "config" / "topics.yaml", self.root / "config" / "topics.yaml")
        sources = {"sources": [{
            "name": "Test RBI", "url": "https://regulator.example/feed.xml", "type": "rss",
            "category": "primary", "authority_level": 1, "topic": "Financial & Banking"
        }]}
        (self.root / "config" / "sources.yaml").write_text(json.dumps(sources), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_feed_dates_and_classification(self):
        entries = parse_feed((ROOT / "tests" / "fixtures" / "feed.xml").read_bytes())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["published_at"].date().isoformat(), "2026-08-30")
        topics = topics_config()
        text = entries[0]["title"] + " " + entries[0]["summary"]
        area = classify(text, topics)
        self.assertEqual(area, "Financial & Banking")
        self.assertTrue(is_meaningful(text, area))
        routine = entries[1]["title"] + " " + entries[1]["summary"]
        self.assertFalse(is_meaningful(routine, classify(routine, topics)))
        macro = "Industrial output growth expanded by 6.7 per cent in July, official data show."
        self.assertEqual(classify(macro, topics), "Macroeconomy, Trade & Public Finance")
        self.assertTrue(is_meaningful(macro, classify(macro, topics)))
        self.assertEqual(classify_signal_type(macro, "Data release"), "Data")
        self.assertEqual(classify_signal_type("Draft regulations invite comments", "Draft"), "Consultation")
        self.assertEqual(classify_signal_type("Bill introduced in Parliament", "Bill introduced"), "Legislative")
        self.assertEqual(classify_signal_type("Auction of State Government Securities", "Announcement", "Institutional"), "Institutional")

    def test_comprehensive_registry_contains_requested_source_families(self):
        sources = sources_config()["sources"]
        names = {source["name"] for source in sources}
        self.assertGreaterEqual(len(sources), 52)
        required = {
            "Reserve Bank of India - Publications RSS", "Open Government Data Platform India",
            "Ministry of Statistics and Programme Implementation", "PRS Legislative Research - Bill Track",
            "Insolvency and Bankruptcy Board of India", "National Payments Corporation of India",
            "Central Board of Indirect Taxes and Customs", "Directorate General of Foreign Trade",
            "Central Drugs Standard Control Organisation", "Central Electricity Authority",
            "Petroleum and Natural Gas Regulatory Board", "Rajya Sabha - Bills", "India Code",
        }
        self.assertTrue(required.issubset(names))

    def test_query_identifiers_become_canonical_source_ids(self):
        source = {"name": "RBI", "short_name": "RBI Releases"}
        from app.collector import Collector
        self.assertEqual(
            Collector._identifier_from_url("https://rbi.example/release?prid=63468", source),
            "RBI Releases/PRID/63468",
        )

    def test_article_extraction_excludes_related_story_navigation(self):
        html = """<nav>Old policy story</nav><article><h1>New rule</h1><div class='entry-content'><p>The regulator notified a new rule with detailed compliance duties for banks and payment firms.</p><p>The rule applies after publication and changes transaction records, disclosures, audit logs and customer notices across covered systems.</p></div></article><footer>Unrelated headlines and contact details</footer>"""
        extracted = article_text(html)
        self.assertIn("detailed compliance duties", extracted)
        self.assertNotIn("Unrelated headlines", extracted)

    def test_official_api_and_dated_rbi_parsers_preserve_provenance(self):
        posts = parse_wordpress_posts(b'''[{"id":42,"date_gmt":"2026-08-30T02:30:00","link":"https://official.example/release","title":{"rendered":"Authority issues draft directions"},"content":{"rendered":"<p>Draft directions set out the proposed framework.</p>"}}]''')
        self.assertEqual(posts[0]["identifier"], "42")
        self.assertEqual(posts[0]["published_at"].date().isoformat(), "2026-08-30")
        table = """<table><tr><td><b>Aug 25, 2026</b></td></tr><tr><td><a href=NotificationUser.aspx?Id=13690&Mode=0>RBI Directions on digital payment security</a></td><td><a href=\"https://rbidocs.rbi.org.in/rule.PDF\">PDF</a></td></tr></table>"""
        notifications = parse_rbi_notifications(table, "https://www.rbi.org.in/Scripts/NotificationUser.aspx")
        self.assertEqual(notifications[0]["identifier"], "RBI/13690")
        self.assertEqual(notifications[0]["url"], "https://rbidocs.rbi.org.in/rule.PDF")
        self.assertEqual(parse_date("Aug 25, 2026").date().isoformat(), "2026-08-25")

    def test_database_creation_and_duplicate_hash(self):
        db = Database(self.root / "data" / "intelligence.db")
        db.initialize()
        item = RawItem("RBI", "primary", 1, "https://example.test/item", "RBI issues circular on KYC", NOW, "RBI circular changes KYC requirements for banks.")
        event = event_from_item(item, item.summary, "Financial & Banking", topics_config(), NOW)
        event.id = db.insert_event(event)
        self.assertIsNotNone(db.find_hash(event.content_hash))
        with self.assertRaises(sqlite3.IntegrityError):
            db.insert_event(event)

    def test_end_to_end_report_history_and_links(self):
        first = Pipeline(root=self.root, now=NOW, client=FakeClient()).run()
        self.assertEqual(first.included, 1)
        content = first.report_path.read_text(encoding="utf-8")
        self.assertIn("India Policy & Regulatory Intelligence Update", content)
        self.assertIn("[Primary source](https://regulator.example/draft-digital-lending)", content)
        self.assertIn("Status:** Draft", content)
        second = Pipeline(root=self.root, now=NOW, client=FakeClient()).run()
        self.assertEqual(second.included, 0)
        self.assertIn("RBI issues draft directions", second.report_path.read_text(encoding="utf-8"))
        with sqlite3.connect(self.root / "data" / "intelligence.db") as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM daily_reports").fetchone()[0], 1)

    def test_source_failure_is_nonfatal_and_recorded(self):
        result = Pipeline(root=self.root, now=NOW, client=FakeClient(fail=True)).run()
        self.assertEqual(result.included, 0)
        self.assertTrue(result.errors)
        with sqlite3.connect(self.root / "data" / "intelligence.db") as conn:
            count, error = conn.execute("SELECT failure_count,last_error FROM sources WHERE name='Test RBI'").fetchone()
            self.assertEqual(count, 1)
            self.assertIn("simulated timeout", error)

    def test_successful_empty_source_is_not_marked_failed(self):
        sources = {"sources": [{
            "name": "Empty feed", "url": "https://regulator.example/empty.xml", "type": "rss",
            "category": "primary", "authority_level": 1, "topic": "Financial & Banking"
        }]}
        (self.root / "config" / "sources.yaml").write_text(json.dumps(sources), encoding="utf-8")

        class EmptyClient:
            def get(self, url):
                return Response(url, 200, "application/rss+xml", b"<rss><channel><title>Empty</title></channel></rss>")

        Pipeline(root=self.root, now=NOW, client=EmptyClient()).run()
        with sqlite3.connect(self.root / "data" / "intelligence.db") as conn:
            failures, success = conn.execute("SELECT failure_count,last_success FROM sources WHERE name='Empty feed'").fetchone()
            self.assertEqual(failures, 0)
            self.assertIsNotNone(success)

    def test_material_status_change_links_to_history(self):
        pipeline = Pipeline(root=self.root, now=NOW, client=FakeClient())
        pipeline.db.initialize()
        topics = topics_config()
        draft_item = RawItem("RBI", "primary", 1, "https://example.test/rule", "RBI draft regulation on digital lending", NOW, "Draft regulation for banks and NBFC digital lending.", "RBI/2026/101")
        final_item = RawItem("RBI", "primary", 1, "https://example.test/rule-final", "RBI notifies regulation on digital lending", NOW, "RBI notification issues final regulation for banks and NBFC digital lending.", "RBI/2026/101")
        first = pipeline._store_if_new(event_from_item(draft_item, draft_item.summary, "Financial & Banking", topics, NOW))
        second = pipeline._store_if_new(event_from_item(final_item, final_item.summary, "Financial & Banking", topics, NOW))
        self.assertIsNotNone(first)
        self.assertTrue(second.is_update)
        self.assertEqual(second.previous_event_id, first.id)

    def test_equivalent_official_paths_do_not_create_a_false_update(self):
        pipeline = Pipeline(root=self.root, now=NOW, client=FakeClient())
        pipeline.db.initialize()
        topics = topics_config()
        first_item = RawItem("Official feed", "official", 2, "https://example.test/release", "RBI issues directions on digital lending", NOW, "RBI issues directions on digital lending for banks and NBFCs.", "feed-100")
        alternate_item = RawItem("Official API", "official", 2, "https://example.test/release", "RBI issues directions on digital lending", NOW, "RBI issues directions on digital lending for banks and NBFCs.", "api-100")
        first = pipeline._store_if_new(event_from_item(first_item, first_item.summary, "Financial & Banking", topics, NOW))
        second = pipeline._store_if_new(event_from_item(alternate_item, alternate_item.summary, "Financial & Banking", topics, NOW))
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_similar_directions_with_distinct_ids_remain_distinct(self):
        pipeline = Pipeline(root=self.root, now=NOW, client=FakeClient())
        pipeline.db.initialize()
        topics = topics_config()
        rural = RawItem("RBI", "primary", 1, "https://rbi.example/rural", "RBI Rural Co-operative Banks CRR Directions", NOW, "RBI/2026/242 issues directions for rural co-operative banks.", "RBI/2026/242")
        commercial = RawItem("RBI", "primary", 1, "https://rbi.example/commercial", "RBI Commercial Banks CRR Directions", NOW, "RBI/2026/238 issues directions for commercial banks.", "RBI/2026/238")
        first = pipeline._store_if_new(event_from_item(rural, rural.summary, "Financial & Banking", topics, NOW))
        second = pipeline._store_if_new(event_from_item(commercial, commercial.summary, "Financial & Banking", topics, NOW))
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertFalse(second.is_update)

    def test_source_alerts_begin_after_three_failures(self):
        db = Database(self.root / "data" / "intelligence.db")
        db.initialize()
        db.sync_sources([{"name": "Unstable feed", "url": "https://example.test/feed", "type": "rss", "authority_level": 1}])
        for index in range(3):
            db.source_result("Unstable feed", f"2026-08-30T0{index}:00:00+05:30", False, "timeout")
        self.assertEqual(db.source_alerts()[0]["name"], "Unstable feed")

    def test_watchlist_max_four(self):
        topics = topics_config()
        events = []
        for n in range(5):
            item = RawItem("RBI", "primary", 1, f"https://example.test/{n}", f"RBI draft regulation {n} on digital lending", NOW, "Draft regulation for banks and NBFC digital lending compliance.")
            event = event_from_item(item, item.summary, "Financial & Banking", topics, NOW)
            event.id = n + 1
            events.append(event)
        text = render_report(NOW, [], [dict(id=e.id, canonical_title=e.canonical_title, status=e.status, deadline=e.deadline, primary_source_url=e.primary_source_url) for e in events])
        self.assertEqual(text.count("### [RBI draft regulation"), 4)

    def test_editorial_gate_rejects_thin_event(self):
        item = RawItem("RBI", "primary", 1, "https://example.test/thin", "RBI circular on KYC", NOW, "Short notice.")
        event = event_from_item(item, item.summary, "Financial & Banking", topics_config(), NOW)
        event.description = "Too short to establish what changed."
        self.assertIn("insufficient factual detail", publication_issues(event))

    def test_editorial_gate_rejects_unrelated_site_pdf(self):
        item = RawItem("Official news", "official", 2, "https://example.test/charter.pdf", "Consumer Affairs notifies Legal Metrology Rules", NOW, "")
        event = event_from_item(item, "", "Deregulation & Ease of Doing Business", topics_config(), NOW)
        event.description = "Citizen's Charter for programme booking and recording at All India Radio stations, with contact details for engineering and news rooms across India. This general document explains service contacts, complaints, offices, telephone numbers, email addresses and programme procedures for listeners and visitors. It does not describe a regulatory notification or legal metrology obligations."
        self.assertIn("site-wide boilerplate mistaken for evidence", publication_issues(event))

    def test_pdf_selection_requires_title_relevance(self):
        html = '<a href="/Citizens-Charter.pdf">PDF</a><a href="/Standardised-framework-NPS.pdf">Download PDF</a>'
        selected = Pipeline._first_pdf_url(html, "https://example.test/page", "Standardised framework for NPS schemes")
        self.assertEqual(selected, "https://example.test/Standardised-framework-NPS.pdf")


if __name__ == "__main__":
    unittest.main()
