from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

import server


def fake_stateless_llm_wiki(user_text, facts, evidence, conflicts):
    fact_types = {fact["type"] for fact in facts}
    lines = [
        "# Personalized Wiki for Juniper & Finch Coffee",
        "",
        "## Bottom Line",
        "Generated from the current textarea and the current retrieved review evidence.",
        "",
        "## Best Fit",
    ]
    if "work_laptop" in fact_types:
        lines.append("- weekday 60-120 minute work blocks, especially Tue-Thu early afternoon")
    if "conversation_meetup" in fact_types:
        lines.append("- low-pressure one-on-one meetups outside brunch rush")
    if "likes_manual_brew" in fact_types:
        lines.append("- a drink-focused stop where single-origin pour-over can carry the visit")
    if "work_laptop" not in fact_types and "conversation_meetup" not in fact_types and "likes_manual_brew" not in fact_types:
        lines.append("- a short neighborhood coffee stop when timing is flexible")

    lines.extend(["", "## Food / Drink Match"])
    if "likes_matcha" in fact_types:
        lines.append("- Sesame matcha is a strong match.")
    if "likes_oat_milk" in fact_types or "diet_dairy_sensitive" in fact_types:
        lines.append("- Oat milk drinks are relevant for dairy-sensitive orders.")
    if "likes_manual_brew" in fact_types:
        lines.append("- Order the single-origin pour-over first; cortado is the fallback.")
    if "likes_savory_breakfast" in fact_types:
        lines.append("- For savory food, prioritize miso mushroom toast or kimchi egg sandwich.")

    lines.extend(["", "## Logistics"])
    if "transport_no_car" in fact_types:
        lines.append("- No-car access is favorable because the shop is near Muni and walkable stops.")
    if "transport_has_car" in fact_types:
        lines.append("- Driving is the weak point because parking is unreliable.")
    for conflict in conflicts:
        lines.append(f"- Wi-Fi conflict handling: using latest review {conflict['winnerReviewId']}.")

    lines.extend(["", "## Things To Avoid"])
    if "dislikes_lines" in fact_types:
        lines.append("- Avoid Sat-Sun 10:00 AM - 1:00 PM if long lines would ruin the visit.")
    else:
        lines.append("- Avoid peak brunch if seating matters.")

    lines.extend(["", "## Suggested Visit Plan"])
    if "likes_manual_brew" in fact_types:
        lines.append("Go weekday early afternoon. Order single-origin pour-over first.")
    else:
        lines.append("Go weekday early afternoon and treat it as a short first test.")

    lines.extend(["", "## Recommended Actions For You"])
    if "transport_has_car" in fact_types:
        lines.append("- Check parking before leaving and keep the paid garage as a backup, not the default.")
    if "dislikes_lines" in fact_types:
        lines.append("- Skip Sat-Sun 10:00 AM - 1:00 PM and use weekday afternoon as the first test window.")
    if "likes_manual_brew" in fact_types:
        lines.append("- Order the single-origin pour-over first; use cortado as the fallback if pour-over is unavailable.")
    if "work_laptop" in fact_types:
        lines.append("- Choose the back rail first, confirm outlet availability, and keep a backup for video calls.")

    lines.extend(["", "## Evidence From Reviews"])
    for review in evidence[:3]:
        lines.append(f"- {review['id']}: {review['body'][:120]}")
    return "\n".join(lines), {"mode": "llm-stateless-test", "detail": "Test double for stateless LLM output."}


class DemoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.originals = {
            "BASE_DIR": server.BASE_DIR,
            "PUBLIC_DIR": server.PUBLIC_DIR,
            "DATA_DIR": server.DATA_DIR,
            "WIKI_DIR": server.WIKI_DIR,
            "STATE_FILE": server.STATE_FILE,
        }
        self.original_try_llm_wiki = server.try_llm_wiki
        server.try_llm_wiki = fake_stateless_llm_wiki
        base = Path(self.tmp.name)
        server.BASE_DIR = base
        server.PUBLIC_DIR = base / "public"
        server.DATA_DIR = base / "data"
        server.WIKI_DIR = base / "wiki"
        server.STATE_FILE = base / "demo_state.json"

    def tearDown(self) -> None:
        server.try_llm_wiki = self.original_try_llm_wiki
        for name, value in self.originals.items():
            setattr(server, name, value)
        self.tmp.cleanup()

    def test_reset_creates_default_wiki_and_reviews(self) -> None:
        state = server.reset_demo()
        self.assertEqual(state["reviewCount"], 420)
        self.assertIn("Personalized Wiki for Juniper & Finch Coffee", state["wiki"])
        self.assertIn("Personalized Wiki for Juniper & Finch Coffee", state["defaultWiki"])
        self.assertEqual(state["facts"], [])

    def test_no_car_user_adds_transit_and_parking_evidence(self) -> None:
        result = server.generate_personalized_wiki(server.DEFAULT_USER_TEXT + "\nI really do not have a car.")
        self.assertTrue(any(fact["type"] == "transport_no_car" for fact in result["facts"]))
        self.assertIn("No-car access is favorable", result["wiki"])
        tags = {tag for review in result["evidence"] for tag in review["tags"]}
        self.assertTrue({"transit", "parking"} & tags)

    def test_dairy_and_matcha_preferences_change_food_section(self) -> None:
        text = "Name: Mina. I like matcha and oat milk. I am lactose sensitive."
        result = server.generate_personalized_wiki(text)
        self.assertIn("Sesame matcha", result["wiki"])
        self.assertIn("Oat milk", result["wiki"])
        self.assertTrue(any(fact["type"] == "diet_dairy_sensitive" for fact in result["facts"]))

    def test_dislikes_lines_adds_weekend_avoidance(self) -> None:
        result = server.generate_personalized_wiki("Name: Mina. I hate long lines and waiting.")
        self.assertTrue(any(fact["type"] == "dislikes_lines" for fact in result["facts"]))
        self.assertIn("Avoid Sat-Sun 10:00 AM - 1:00 PM", result["wiki"])

    def test_conflicting_review_information_uses_latest_review(self) -> None:
        text = "Name: Mina. I carry a laptop and need Wi-Fi for video calls."
        result = server.generate_personalized_wiki(text)
        self.assertTrue(result["conflicts"])
        wifi_conflict = next(item for item in result["conflicts"] if item["topic"] == "wifi")
        self.assertEqual(wifi_conflict["rule"], "latest-review-wins")
        self.assertIn(wifi_conflict["winnerReviewId"], result["wiki"])
        self.assertIn("using latest review", result["wiki"].lower())

    def test_redis_unavailable_path_still_generates(self) -> None:
        result = server.generate_personalized_wiki(server.DEFAULT_USER_TEXT)
        self.assertIn(result["status"]["index"]["mode"], {"local", "redis-hash", "redis-search"})
        self.assertIn(result["status"]["sessionMemory"]["mode"], {"local", "redis-hash", "redis-search", "error"})
        self.assertIn(result["status"]["memory"]["mode"], {"not-installed", "needs-llm-api-key", "ready", "cognee", "error"})
        self.assertIn("## Bottom Line", result["wiki"])

    def test_without_llm_key_does_not_use_rule_based_personalized_wiki(self) -> None:
        server.try_llm_wiki = self.original_try_llm_wiki
        old_llm_key = os.environ.pop("LLM_API_KEY", None)
        old_openai_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            result = server.generate_personalized_wiki(server.DEFAULT_USER_TEXT)
        finally:
            if old_llm_key is not None:
                os.environ["LLM_API_KEY"] = old_llm_key
            if old_openai_key is not None:
                os.environ["OPENAI_API_KEY"] = old_openai_key
            server.try_llm_wiki = fake_stateless_llm_wiki
        self.assertIn("LLM Required", result["wiki"])
        self.assertNotIn("weekday 60-120 minute work blocks", result["wiki"])
        self.assertEqual(result["status"]["generator"]["mode"], "llm-required")

    def test_cognee_status_is_exposed(self) -> None:
        state = server.reset_demo()
        self.assertIn("memoryStatus", state)
        self.assertIn("mode", state["memoryStatus"])

    def test_boyfriend_manual_brew_info_changes_wiki(self) -> None:
        text = "Name: Mina. She want to meet his boy friend who prefer special manual."
        result = server.generate_personalized_wiki(text)
        fact_types = {fact["type"] for fact in result["facts"]}
        self.assertIn("conversation_meetup", fact_types)
        self.assertIn("likes_manual_brew", fact_types)
        self.assertIn("single-origin pour-over", result["wiki"])
        self.assertIn("one-on-one meetups", result["wiki"])
        self.assertIn("## Recommended Actions For You", result["wiki"])
        self.assertIn("Order the single-origin pour-over first", result["wiki"])

    def test_recommended_actions_follow_user_constraints(self) -> None:
        text = "Name: Leo. He drives and hates waiting in a long line."
        result = server.generate_personalized_wiki(text)
        self.assertIn("## Recommended Actions For You", result["wiki"])
        self.assertIn("Check parking before leaving", result["wiki"])
        self.assertIn("Skip Sat-Sun 10:00 AM - 1:00 PM", result["wiki"])

    def test_deleted_laptop_info_removes_work_block_recommendation(self) -> None:
        text = """Name: Mina Zhang.
Birth date: 1992-07-18.
Mina is a freelance UX researcher who often works between client calls.
She does not have a car and usually gets around by Muni or walking.
She likes sesame matcha, oat milk drinks, and savory breakfast.
She is lactose sensitive.
She dislikes long lines and loud rooms.
She want to meet his boy friend who prefer special manual."""
        result = server.generate_personalized_wiki(text)
        fact_types = {fact["type"] for fact in result["facts"]}
        self.assertNotIn("work_laptop", fact_types)
        self.assertNotIn("weekday 60-120 minute work blocks", result["wiki"])
        self.assertNotIn("Choose the back rail first", result["wiki"])
        self.assertIn("single-origin pour-over", result["wiki"])

    def test_explicit_laptop_info_still_adds_work_block_recommendation(self) -> None:
        result = server.generate_personalized_wiki(server.DEFAULT_USER_TEXT)
        self.assertTrue(any(fact["type"] == "work_laptop" for fact in result["facts"]))
        self.assertIn("weekday 60-120 minute work blocks", result["wiki"])

    def test_reviews_payload_contains_all_reviews(self) -> None:
        server.reset_demo()
        payload = server.get_reviews_payload()
        self.assertEqual(payload["reviewCount"], 420)
        self.assertEqual(len(payload["reviews"]), 420)
        self.assertIn("reviewDate", payload["reviews"][0])
        self.assertIn("tags", payload["reviews"][0])

    def test_added_review_is_retrieved_for_same_user_info(self) -> None:
        server.reset_demo()
        payload = server.add_review(
            {
                "body": (
                    "Brand new review: the single-origin manual pour-over was excellent for a quiet date. "
                    "There was no line on a weekday afternoon, and the small table worked well for conversation."
                ),
                "rating": 5,
                "reviewDate": "2026-05-16",
                "tags": "manual quiet date no-line",
            }
        )
        self.assertEqual(payload["reviewCount"], 421)
        self.assertEqual(payload["addedReview"]["id"], "r421")
        result = server.generate_personalized_wiki(
            "Name: Mina. She wants to meet her boyfriend who prefers special manual brew coffee. She dislikes long lines."
        )
        evidence_ids = {review["id"] for review in result["evidence"]}
        self.assertIn("r421", evidence_ids)
        self.assertIn("r421", result["wiki"])

    def test_deleted_review_is_removed_from_same_user_info_retrieval(self) -> None:
        server.reset_demo()
        server.add_review(
            {
                "body": (
                    "Brand new review: the Panama Gesha manual-brew flight was ideal for a quiet date. "
                    "There was no line, and the tasting notes made the visit feel special."
                ),
                "rating": 5,
                "reviewDate": "2026-05-16",
                "tags": "manual-brew gesha quiet date no-line",
            }
        )
        text = "Name: Mina. She wants to meet her boyfriend who prefers special manual brew coffee. She dislikes long lines."
        before = server.generate_personalized_wiki(text)
        before_ids = {review["id"] for review in before["evidence"]}
        self.assertIn("r421", before_ids)

        deleted = server.delete_review("r421")
        self.assertEqual(deleted["reviewCount"], 420)
        self.assertEqual(deleted["deletedReview"]["id"], "r421")
        self.assertNotIn("r421", {review["id"] for review in deleted["reviews"]})

        after = server.generate_personalized_wiki(text)
        after_ids = {review["id"] for review in after["evidence"]}
        self.assertNotIn("r421", after_ids)
        self.assertNotIn("r421", after["wiki"])


if __name__ == "__main__":
    unittest.main()
