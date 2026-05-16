from __future__ import annotations

import json
import math
import os
import re
import shutil
import asyncio
import contextlib
import hashlib
import io
import threading
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_DIR = BASE_DIR / "data"
WIKI_DIR = BASE_DIR / "wiki"
STATE_FILE = BASE_DIR / "demo_state.json"
DEFAULT_PORT = 8891
REDIS_PREFIX = "pbw:review:"
REDIS_INDEX = "idx:pbw:reviews"
REDIS_SESSION_PREFIX = "pbw:session:"
COGNEE_DATASET = "personalized_business_wiki_demo"


def load_local_env() -> None:
    for path in (BASE_DIR / ".env", BASE_DIR / ".env.local", BASE_DIR.parent / ".env", BASE_DIR.parent / ".env.local"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_local_env()


def ensure_openai_key_alias() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    api_key = os.environ.get("LLM_API_KEY")
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key


BUSINESS = {
    "name": "Juniper & Finch Coffee",
    "category": "Neighborhood coffee shop",
    "address": "214 Linden Lane, Hayes Valley, San Francisco",
    "phone": "(415) 555-0198",
    "hours": {
        "Mon-Fri": "7:00 AM - 6:00 PM",
        "Sat-Sun": "8:00 AM - 5:00 PM",
    },
    "officialClaims": [
        "House-roasted espresso, pour-over, matcha, and seasonal tea drinks.",
        "Small breakfast menu with vegetarian and dairy-free options.",
        "Free Wi-Fi, eight indoor tables, three window seats, and a narrow back rail.",
        "No dedicated parking lot. Dogs are welcome on the sidewalk benches.",
    ],
    "menuHighlights": [
        "oat maple latte",
        "sesame matcha",
        "cortado",
        "miso mushroom toast",
        "kimchi egg sandwich",
        "tahini banana bread",
    ],
}


SURROUNDINGS = {
    "transit": [
        "Two blocks from Van Ness Muni.",
        "Six-minute walk from Hayes & Laguna bus stops.",
        "Bike racks sit beside the pharmacy next door.",
    ],
    "parking": [
        "Street parking turns over slowly after 9:30 AM.",
        "A paid garage is four blocks east, but reviewers call it expensive.",
        "Most nearby two-hour spots are full on weekend brunch hours.",
    ],
    "nearby": [
        "Across from a small design studio cluster.",
        "One block from Patricia's Green.",
        "Next door to a pharmacy and a florist.",
        "Near pre-show foot traffic from the performing arts district.",
    ],
    "localRhythm": [
        "Quietest review window: Tue-Thu, 1:30 PM - 4:00 PM.",
        "Busiest review window: Sat-Sun, 10:00 AM - 1:00 PM.",
        "Rainy afternoons push more people indoors and make the shop louder.",
    ],
}


DEFAULT_USER_TEXT = """Name: Mina Zhang.
Birth date: 1992-07-18.
Mina is a freelance UX researcher who often works between client calls.
She does not have a car and usually gets around by Muni or walking.
She often carries a laptop and wants a calm place for 60-120 minute work blocks.
She likes sesame matcha, oat milk drinks, and savory breakfast.
She is lactose sensitive.
She dislikes long lines and loud rooms."""


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "can",
    "do",
    "for",
    "from",
    "get",
    "good",
    "have",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "the",
    "there",
    "they",
    "this",
    "to",
    "with",
    "without",
    "would",
}


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:72] or "wiki"


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in STOPWORDS and len(token) > 1]


def ensure_dirs() -> None:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cognee_status_path() -> Path:
    return BASE_DIR / "cognee_status.json"


def bullet_list(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def generate_reviews() -> list[dict[str, Any]]:
    base_date = datetime(2025, 12, 20)
    products = [
        ("oat maple latte", ["oat-milk", "espresso"]),
        ("sesame matcha", ["matcha", "dairy-free"]),
        ("cortado", ["espresso"]),
        ("single-origin pour-over", ["pour-over"]),
        ("miso mushroom toast", ["savory-breakfast", "vegetarian"]),
        ("kimchi egg sandwich", ["savory-breakfast"]),
        ("tahini banana bread", ["pastry", "dairy-free"]),
        ("cold brew", ["cold-drink"]),
        ("ginger pear latte", ["seasonal"]),
        ("decaf cappuccino", ["decaf"]),
        ("chai with oat milk", ["oat-milk", "tea"]),
        ("avocado toast", ["savory-breakfast", "vegetarian"]),
    ]
    situations = [
        ("weekday laptop session", ["laptop", "wifi", "outlets"]),
        ("quick morning pickup", ["quick-stop", "line"]),
        ("weekend brunch", ["weekend", "line", "seating"]),
        ("quiet one-on-one catchup", ["conversation", "quiet"]),
        ("first date", ["date", "ambience"]),
        ("post-yoga drink", ["quick-stop", "healthy"]),
        ("rainy afternoon", ["weather", "indoor-seating"]),
        ("stroller stop", ["kid-friendly", "seating"]),
        ("dog walk break", ["dog-friendly", "outdoor-seating"]),
        ("pre-show coffee", ["evening", "line"]),
        ("client prep hour", ["laptop", "quiet", "wifi"]),
        ("solo reading break", ["quiet", "seating"]),
    ]
    tones = [
        ("The barista remembered names and the room felt calm.", 5, ["service", "quiet"]),
        ("The drink was excellent, though the tables turned over slowly.", 4, ["quality", "seating"]),
        ("Food was better than expected, but the counter line moved in waves.", 4, ["food", "line"]),
        ("The flavors were thoughtful; the room got loud once groups arrived.", 4, ["quality", "noise"]),
        ("Wi-Fi worked for browsing but struggled during a video call.", 3, ["wifi", "mixed"]),
        ("Outlets were hard to claim unless you came after lunch.", 3, ["outlets", "timing"]),
        ("Street parking was frustrating, but transit access made up for it.", 3, ["parking", "transit"]),
        ("Weekend energy was fun, but not the right mood for focused work.", 3, ["weekend", "noise"]),
        ("The pastry was dry and the wait felt longer than the order deserved.", 2, ["pastry", "line"]),
        ("Great drink, tiny tables, and a little pressure to free the seat.", 3, ["seating", "laptop"]),
    ]
    time_windows = [
        ("Monday 8:10 AM", ["weekday", "morning"]),
        ("Tuesday 2:15 PM", ["weekday", "afternoon", "quiet"]),
        ("Wednesday 3:40 PM", ["weekday", "afternoon", "quiet"]),
        ("Thursday 11:20 AM", ["weekday", "late-morning"]),
        ("Friday 5:10 PM", ["weekday", "evening"]),
        ("Saturday 10:45 AM", ["weekend", "brunch"]),
        ("Saturday 1:30 PM", ["weekend", "afternoon"]),
        ("Sunday 9:35 AM", ["weekend", "morning"]),
        ("Sunday 3:05 PM", ["weekend", "afternoon"]),
    ]
    access_notes = [
        ("I walked from Van Ness and it was easy.", ["transit", "walkable"]),
        ("A friend circled for parking while I ordered.", ["parking"]),
        ("The bike rack by the pharmacy was open.", ["bike", "walkable"]),
        ("Rideshare drop-off was painless on the side street.", ["rideshare"]),
        ("The paid garage nearby felt overpriced for a coffee run.", ["parking"]),
        ("The Hayes bus stop made it simple without a car.", ["transit"]),
    ]
    comfort_notes = [
        ("The back rail was best for laptop work.", ["laptop", "outlets"]),
        ("Window seats were pleasant but cramped for a laptop.", ["seating", "laptop"]),
        ("Two outlets were free in the early afternoon.", ["outlets", "timing"]),
        ("Music was low enough for a quiet conversation.", ["quiet", "conversation"]),
        ("Weekend chatter bounced off the tile walls.", ["noise", "weekend"]),
        ("Staff did not rush me, but I bought a second drink.", ["laptop", "service"]),
    ]

    reviews: list[dict[str, Any]] = []
    for index in range(420):
        product, product_tags = products[index % len(products)]
        situation, situation_tags = situations[(index * 5) % len(situations)]
        tone, base_rating, tone_tags = tones[(index * 7) % len(tones)]
        visit_time, time_tags = time_windows[(index * 11) % len(time_windows)]
        access, access_tags = access_notes[(index * 13) % len(access_notes)]
        comfort, comfort_tags = comfort_notes[(index * 17) % len(comfort_notes)]
        rating = max(1, min(5, base_rating + (1 if index % 19 == 0 else 0) - (1 if index % 23 == 0 else 0)))
        tags = sorted(set(product_tags + situation_tags + tone_tags + time_tags + access_tags + comfort_tags))
        body = (
            f"I visited Juniper & Finch for a {situation} on {visit_time}. "
            f"I ordered the {product}. {tone} {access} {comfort}"
        )
        if index % 8 == 0:
            body += " The oat milk option tasted better than most nearby cafes."
            tags = sorted(set(tags + ["oat-milk", "dairy-free"]))
        if index % 10 == 0:
            body += " I would avoid peak brunch if you dislike lines."
            tags = sorted(set(tags + ["line", "timing"]))
        if index % 13 == 0:
            body += " It felt especially useful for a short solo work block."
            tags = sorted(set(tags + ["laptop", "quiet"]))
        if index % 17 == 0:
            body += " The matcha had a nutty sesame note without being too sweet."
            tags = sorted(set(tags + ["matcha"]))
        if index in {14, 52, 118}:
            body += " Older note: the Wi-Fi dropped twice and felt unreliable for calls."
            rating = min(rating, 3)
            tags = sorted(set(tags + ["wifi", "unreliable-wifi", "mixed"]))
        if index in {389, 405, 417}:
            body += " Updated recent visit: the Wi-Fi seemed upgraded and held a full video call."
            rating = max(rating, 4)
            tags = sorted(set(tags + ["wifi", "reliable-wifi", "recent"]))
        review_date = (base_date + timedelta(days=index // 3)).strftime("%Y-%m-%d")
        reviews.append(
            {
                "id": f"r{index + 1:03d}",
                "reviewer": f"reviewer_{index + 1:03d}",
                "reviewDate": review_date,
                "rating": rating,
                "product": product,
                "situation": situation,
                "visitTime": visit_time,
                "tags": tags,
                "body": body,
            }
        )
    return reviews


def load_reviews() -> list[dict[str, Any]]:
    ensure_dirs()
    path = DATA_DIR / "reviews.json"
    if not path.exists():
        reviews = generate_reviews()
        write_json(path, reviews)
        return reviews
    return json.loads(path.read_text(encoding="utf-8"))


def next_review_id(reviews: list[dict[str, Any]]) -> str:
    max_id = 0
    for review in reviews:
        match = re.match(r"r(\d+)$", str(review.get("id", "")))
        if match:
            max_id = max(max_id, int(match.group(1)))
    return f"r{max_id + 1:03d}"


def normalize_review_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_tags = re.split(r"[,#\s]+", value)
    elif isinstance(value, list):
        raw_tags = [str(item) for item in value]
    else:
        raw_tags = []
    tags = []
    for raw_tag in raw_tags:
        tag = re.sub(r"[^a-z0-9-]+", "-", raw_tag.lower()).strip("-")
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def infer_review_tags(body: str) -> list[str]:
    lower = body.lower()
    mapping = [
        ("matcha", ["matcha"]),
        ("抹茶", ["matcha"]),
        ("oat milk", ["oat-milk", "dairy-free"]),
        ("oat-milk", ["oat-milk", "dairy-free"]),
        ("lactose", ["dairy-free"]),
        ("dairy-free", ["dairy-free"]),
        ("manual", ["pour-over", "quality"]),
        ("manual brew", ["pour-over", "quality"]),
        ("hand brew", ["pour-over", "quality"]),
        ("hand-brew", ["pour-over", "quality"]),
        ("pour over", ["pour-over", "quality"]),
        ("pour-over", ["pour-over", "quality"]),
        ("single origin", ["pour-over", "quality"]),
        ("single-origin", ["pour-over", "quality"]),
        ("specialty", ["pour-over", "quality"]),
        ("手冲", ["pour-over", "quality"]),
        ("quiet", ["quiet"]),
        ("calm", ["quiet"]),
        ("conversation", ["conversation", "quiet"]),
        ("date", ["date", "ambience", "conversation"]),
        ("boyfriend", ["date", "conversation"]),
        ("partner", ["date", "conversation"]),
        ("line", ["line"]),
        ("queue", ["line"]),
        ("wait", ["line", "timing"]),
        ("brunch", ["brunch", "weekend"]),
        ("weekend", ["weekend"]),
        ("weekday", ["weekday"]),
        ("parking", ["parking"]),
        ("garage", ["parking"]),
        ("muni", ["transit"]),
        ("bus", ["transit"]),
        ("walk", ["walkable"]),
        ("laptop", ["laptop"]),
        ("outlet", ["outlets"]),
        ("wifi", ["wifi"]),
        ("wi-fi", ["wifi"]),
        ("video call", ["wifi", "laptop"]),
        ("toast", ["savory-breakfast", "food"]),
        ("sandwich", ["savory-breakfast", "food"]),
        ("breakfast", ["savory-breakfast", "food"]),
    ]
    tags: list[str] = []
    for needle, inferred in mapping:
        if needle in lower:
            tags.extend(inferred)
    return sorted(set(tags))


def infer_review_product(body: str) -> str:
    lower = body.lower()
    for product in BUSINESS["menuHighlights"]:
        if product in lower:
            return product
    if contains_any(lower, ["manual", "hand brew", "pour over", "pour-over", "single origin", "single-origin", "手冲"]):
        return "single-origin pour-over"
    if "matcha" in lower or "抹茶" in lower:
        return "sesame matcha"
    if "oat" in lower:
        return "oat maple latte"
    return "custom review item"


def infer_review_situation(body: str) -> str:
    lower = body.lower()
    if contains_any(lower, ["date", "boyfriend", "partner", "conversation", "one-on-one"]):
        return "quiet one-on-one catchup"
    if contains_any(lower, ["laptop", "work", "video call", "wifi", "wi-fi"]):
        return "weekday laptop session"
    if contains_any(lower, ["brunch", "breakfast", "weekend"]):
        return "weekend brunch"
    return "user-added visit"


def normalize_review_date(value: Any) -> str:
    text = str(value or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return datetime.now().strftime("%Y-%m-%d")


def add_review(payload: dict[str, Any]) -> dict[str, Any]:
    reviews = load_reviews()
    body = str(payload.get("body") or payload.get("review") or "").strip()
    if len(body) < 12:
        raise ValueError("Review body must be at least 12 characters.")
    try:
        rating = int(payload.get("rating", 5))
    except (TypeError, ValueError):
        rating = 5
    rating = max(1, min(5, rating))
    explicit_tags = normalize_review_tags(payload.get("tags", []))
    tags = sorted(set(explicit_tags + infer_review_tags(body) + ["user-added"]))
    review = {
        "id": next_review_id(reviews),
        "reviewer": str(payload.get("reviewer") or "demo_user_added").strip() or "demo_user_added",
        "reviewDate": normalize_review_date(payload.get("reviewDate")),
        "rating": rating,
        "product": str(payload.get("product") or infer_review_product(body)).strip(),
        "situation": str(payload.get("situation") or infer_review_situation(body)).strip(),
        "visitTime": str(payload.get("visitTime") or "user-added visit").strip(),
        "tags": tags,
        "body": body,
    }
    reviews.append(review)
    write_json(DATA_DIR / "reviews.json", reviews)
    index_status = ReviewIndex(reviews).index()
    state = read_json(STATE_FILE, default_state())
    state["indexed"] = True
    state["indexStatus"] = index_status
    state["updatedAt"] = now_stamp()
    write_json(STATE_FILE, state)
    return {
        **get_reviews_payload(),
        "addedReview": compact_review(review),
        "indexStatus": index_status,
    }


def review_search_text(review: dict[str, Any]) -> str:
    return " ".join(
        [
            str(review.get("body", "")),
            str(review.get("product", "")),
            str(review.get("situation", "")),
            str(review.get("visitTime", "")),
            " ".join(review.get("tags", [])),
        ]
    )


class LocalReviewIndex:
    def __init__(self, reviews: list[dict[str, Any]]):
        self.reviews = reviews
        self.documents = []
        document_frequency: Counter[str] = Counter()
        for review in reviews:
            tokens = set(tokenize(review_search_text(review)))
            self.documents.append(tokens)
            for token in tokens:
                document_frequency[token] += 1
        self.idf = {
            token: math.log((1 + len(reviews)) / (1 + count)) + 1
            for token, count in document_frequency.items()
        }

    def search(self, query: str, tags: list[str], limit: int = 6) -> list[dict[str, Any]]:
        query_counts = Counter(tokenize(query))
        scored = []
        for review, tokens in zip(self.reviews, self.documents):
            score = 0.0
            for token, count in query_counts.items():
                if token in tokens:
                    score += count * self.idf.get(token, 1.0)
            score += sum(1.8 for tag in tags if tag in review["tags"])
            if review["rating"] >= 4:
                score += 0.25
            score += review_recency_score(review) * 0.2
            if score > 0:
                item = dict(review)
                item["score"] = round(score, 3)
                scored.append(item)
        if not scored:
            scored = [dict(review, score=0.0) for review in self.reviews[:limit]]
        scored.sort(key=lambda item: (-item["score"], -item["rating"], item["id"]))
        return scored[:limit]

    def score_review(self, review: dict[str, Any], query: str, tags: list[str]) -> float:
        tokens = set(tokenize(review_search_text(review)))
        query_counts = Counter(tokenize(query))
        score = 0.0
        for token, count in query_counts.items():
            if token in tokens:
                score += count * self.idf.get(token, 1.0)
        review_tags = set(review.get("tags", []))
        score += sum(1.8 for tag in tags if tag in review_tags)
        if int(review.get("rating", 0)) >= 4:
            score += 0.25
        score += review_recency_score(review) * 0.2
        return round(score, 3)


class ReviewIndex:
    def __init__(self, reviews: list[dict[str, Any]]):
        self.reviews = reviews
        self.local = LocalReviewIndex(reviews)
        self.redis_client = None
        self.mode = "local"
        self.detail = "Redis client is not importable, so review retrieval is using the deterministic local scorer."
        self._connect_redis()

    def _connect_redis(self) -> None:
        try:
            import redis  # type: ignore
        except Exception:
            return
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        try:
            client = redis.Redis.from_url(url, decode_responses=True)
            client.ping()
        except Exception as exc:
            self.detail = f"Redis package is installed, but Redis is unavailable: {exc}"
            return
        self.redis_client = client
        self.mode = "redis-hash"
        self.detail = "Redis is connected. Reviews are stored as hashes; RediSearch is used if available."

    def index(self) -> dict[str, str]:
        if self.redis_client is None:
            return {"mode": self.mode, "detail": self.detail}
        client = self.redis_client
        try:
            for key in client.scan_iter(f"{REDIS_PREFIX}*"):
                client.delete(key)
            for review in self.reviews:
                client.hset(
                    f"{REDIS_PREFIX}{review['id']}",
                    mapping={
                        "id": review["id"],
                        "rating": review["rating"],
                        "reviewDate": review["reviewDate"],
                        "product": review["product"],
                        "situation": review["situation"],
                        "visitTime": review["visitTime"],
                        "tags": " ".join(review["tags"]),
                        "body": review["body"],
                    },
                )
            self._ensure_redisearch_index(client)
            return {"mode": self.mode, "detail": self.detail}
        except Exception as exc:
            self.mode = "local"
            self.detail = f"Redis write failed, using local scorer: {exc}"
            return {"mode": self.mode, "detail": self.detail}

    def _ensure_redisearch_index(self, client: Any) -> None:
        try:
            client.execute_command("FT.INFO", REDIS_INDEX)
            self.mode = "redis-search"
            self.detail = f"RedisSearch index {REDIS_INDEX} is ready for review retrieval."
            return
        except Exception:
            pass
        try:
            client.execute_command(
                "FT.CREATE",
                REDIS_INDEX,
                "ON",
                "HASH",
                "PREFIX",
                "1",
                REDIS_PREFIX,
                "SCHEMA",
                "body",
                "TEXT",
                "tags",
                "TEXT",
                "product",
                "TEXT",
                "situation",
                "TEXT",
                "visitTime",
                "TEXT",
                "reviewDate",
                "TEXT",
                "rating",
                "NUMERIC",
            )
            self.mode = "redis-search"
            self.detail = f"RedisSearch index {REDIS_INDEX} was created for review retrieval."
        except Exception as exc:
            self.mode = "redis-hash"
            self.detail = f"Redis hashes are stored and scanned for retrieval; RediSearch is unavailable, so the app scores Redis hash results locally: {exc}"

    def search(self, query: str, tags: list[str], limit: int = 6) -> list[dict[str, Any]]:
        if self.redis_client is None:
            return self.local.search(query, tags, limit)
        if self.mode != "redis-search":
            redis_hash_results = self._redis_hash_search(query, tags, limit)
            if redis_hash_results:
                return redis_hash_results
            return self.local.search(query, tags, limit)
        redis_results = self._redisearch(query, limit * 2)
        if not redis_results:
            return self.local.search(query, tags, limit)
        local_by_id = {item["id"]: item for item in self.local.search(query, tags, len(self.reviews))}
        merged = [local_by_id.get(item["id"], item) for item in redis_results]
        merged.sort(key=lambda item: (-item.get("score", 0), -int(item.get("rating", 0)), item["id"]))
        return merged[:limit]

    def _redis_hash_search(self, query: str, tags: list[str], limit: int) -> list[dict[str, Any]]:
        if self.redis_client is None:
            return []
        scored = []
        try:
            keys = list(self.redis_client.scan_iter(f"{REDIS_PREFIX}*"))
            for key in keys:
                payload = self.redis_client.hgetall(key)
                if not payload:
                    continue
                payload["tags"] = payload.get("tags", "").split()
                payload["rating"] = int(payload.get("rating", 0))
                payload["score"] = self.local.score_review(payload, query, tags)
                payload["retrievalSource"] = "redis-hash"
                if payload["score"] > 0:
                    scored.append(payload)
        except Exception:
            return []
        scored.sort(key=lambda item: (-item.get("score", 0), -int(item.get("rating", 0)), item.get("reviewDate", ""), item["id"]))
        return scored[:limit]

    def _redisearch(self, query: str, limit: int) -> list[dict[str, Any]]:
        if self.redis_client is None:
            return []
        terms = tokenize(query)
        if not terms:
            terms = ["coffee"]
        search_query = " ".join(escape_redis_term(term) for term in terms[:8])
        try:
            raw = self.redis_client.execute_command("FT.SEARCH", REDIS_INDEX, search_query, "LIMIT", "0", str(limit))
        except Exception:
            return []
        results = []
        for index in range(1, len(raw), 2):
            fields = raw[index + 1]
            payload = {fields[i]: fields[i + 1] for i in range(0, len(fields), 2)}
            payload["tags"] = payload.get("tags", "").split()
            payload["rating"] = int(payload.get("rating", 0))
            results.append(payload)
        return results

    def write_session_memory(
        self,
        session_id: str,
        user_text: str,
        facts: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        wiki: str,
    ) -> dict[str, str]:
        if self.redis_client is None:
            return {
                "mode": "local",
                "detail": "Redis session memory is unavailable; local state files still contain the current generation run.",
            }
        key = f"{REDIS_SESSION_PREFIX}{session_id}"
        evidence_payload = [compact_review(review) for review in evidence]
        try:
            pipe = self.redis_client.pipeline()
            pipe.delete(key)
            pipe.hset(
                key,
                mapping={
                    "sessionId": session_id,
                    "business": BUSINESS["name"],
                    "userText": user_text,
                    "factsJson": json.dumps(facts, ensure_ascii=False),
                    "evidenceJson": json.dumps(evidence_payload, ensure_ascii=False),
                    "conflictsJson": json.dumps(conflicts, ensure_ascii=False),
                    "wikiPreview": wiki[:1600],
                    "updatedAt": now_stamp(),
                },
            )
            pipe.expire(key, 60 * 60 * 24)
            pipe.execute()
        except Exception as exc:
            return {"mode": "error", "detail": f"Redis session memory write failed: {exc}"}
        return {
            "mode": self.mode,
            "detail": f"Stored the current generation turn in Redis session memory key {key}; this hot session bundle is distilled into Cognee.",
        }


def escape_redis_term(term: str) -> str:
    return re.sub(r"([^a-zA-Z0-9])", r"\\\1", term)


def review_recency_score(review: dict[str, Any]) -> float:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(review.get("reviewDate", "")))
    if not match:
        return 0.0
    year, month, day = [int(part) for part in match.groups()]
    ordinal = datetime(year, month, day).toordinal()
    base = datetime(2025, 12, 20).toordinal()
    return max(0.0, min(1.0, (ordinal - base) / 140))


def contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def add_fact(facts: list[dict[str, Any]], text: str, fact_type: str, query: str, tags: list[str]) -> None:
    if any(existing["type"] == fact_type and existing["text"] == text for existing in facts):
        return
    facts.append(
        {
            "text": text,
            "type": fact_type,
            "query": query,
            "tags": tags,
            "matchedReviewIds": [],
        }
    )


def parse_user_facts(user_text: str) -> list[dict[str, Any]]:
    text = user_text.strip()
    lower = text.lower()
    facts: list[dict[str, Any]] = []

    name_match = re.search(r"(?:name is|name:|i am|i'm|called)\s*([a-z][a-z\s'-]{1,48})", lower)
    if name_match:
        name = " ".join(part.capitalize() for part in name_match.group(1).split()[:4])
        add_fact(facts, f"User name appears to be {name}.", "identity_name", "personal cafe fit ambience service", ["service", "ambience"])

    birth_match = re.search(r"(?:birth date|birthday|born|出生(?:年月日)?)[^\d]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})", lower)
    if birth_match:
        add_fact(
            facts,
            f"User supplied birth date {birth_match.group(1)}.",
            "identity_birth_date",
            "adult solo cafe ambience seating",
            ["ambience", "seating"],
        )

    if contains_any(lower, ["no car", "without a car", "does not have a car", "don't have a car", "没有车", "没车", "不开车", "无车"]):
        add_fact(
            facts,
            "User does not have a car.",
            "transport_no_car",
            "without car transit walkable muni bus parking frustrating",
            ["transit", "walkable", "parking"],
        )
    elif contains_any(lower, ["has a car", "have a car", "drives", "drive there", "开车", "有车"]):
        add_fact(
            facts,
            "User may drive to the shop.",
            "transport_has_car",
            "parking garage street parking driving weekend",
            ["parking"],
        )

    work_negated = contains_any(
        lower,
        [
            "does not care about laptop",
            "does not care about laptop work",
            "not care about laptop",
            "not looking to work",
            "not working from",
            "不需要办公",
            "不办公",
        ],
    )
    explicit_cafe_work = contains_any(
        lower,
        [
            "laptop",
            "work block",
            "work blocks",
            "work from cafe",
            "work from cafés",
            "work from cafes",
            "work there",
            "remote work",
            "video call",
            "zoom call",
            "wifi for calls",
            "wi-fi for calls",
            "带电脑",
            "电脑",
            "办公",
            "视频会议",
        ],
    )
    if not work_negated and explicit_cafe_work:
        add_fact(
            facts,
            "User may bring a laptop or work from the cafe.",
            "work_laptop",
            "laptop work wifi outlets video call quiet timing",
            ["laptop", "wifi", "outlets", "quiet", "timing"],
        )

    if contains_any(lower, ["quiet", "calm", "not loud", "low noise", "安静", "不吵", "太吵", "吵"]):
        add_fact(
            facts,
            "User prefers a quiet or calm room.",
            "prefers_quiet",
            "quiet calm noise conversation music low weekend loud",
            ["quiet", "noise", "conversation"],
        )

    if contains_any(lower, ["matcha", "抹茶"]):
        add_fact(
            facts,
            "User likes matcha.",
            "likes_matcha",
            "sesame matcha not too sweet nutty drink",
            ["matcha", "dairy-free", "quality"],
        )

    if contains_any(lower, ["oat milk", "oatmilk", "燕麦奶", "燕麦"]):
        add_fact(
            facts,
            "User likes oat milk drinks.",
            "likes_oat_milk",
            "oat milk latte dairy free drink",
            ["oat-milk", "dairy-free", "quality"],
        )

    if contains_any(lower, ["savory", "breakfast", "brunch", "toast", "sandwich", "咸口", "早餐", "早午餐"]):
        add_fact(
            facts,
            "User is interested in savory breakfast or brunch food.",
            "likes_savory_breakfast",
            "savory breakfast toast sandwich food brunch",
            ["savory-breakfast", "food", "weekend"],
        )

    if contains_any(lower, ["lactose", "dairy", "milk allergy", "不能喝牛奶", "乳糖", "忌口", "过敏"]):
        add_fact(
            facts,
            "User has a dairy or lactose constraint.",
            "diet_dairy_sensitive",
            "lactose dairy free oat milk matcha pastry",
            ["dairy-free", "oat-milk", "matcha"],
        )

    if contains_any(lower, ["long line", "lines", "queue", "wait", "排队", "等太久", "久等"]):
        add_fact(
            facts,
            "User dislikes long lines or waiting.",
            "dislikes_lines",
            "avoid long lines wait weekend brunch timing peak",
            ["line", "timing", "weekend"],
        )

    if contains_any(lower, ["date", "catchup", "one-on-one", "conversation", "boyfriend", "boy friend", "partner", "meet his boy", "meet her boy", "聊天", "约会", "见朋友", "男朋友", "伴侣"]):
        add_fact(
            facts,
            "The visit may be a date, partner meetup, or small conversation.",
            "conversation_meetup",
            "quiet one-on-one catchup date conversation ambience",
            ["conversation", "quiet", "ambience"],
        )

    if contains_any(lower, ["manual", "manual brew", "hand brew", "hand-brew", "pour over", "pour-over", "single origin", "single-origin", "special coffee", "specialty coffee", "手冲", "手作", "精品咖啡", "单品"]):
        add_fact(
            facts,
            "A companion or visit preference points toward manual brew, pour-over, or specialty coffee.",
            "likes_manual_brew",
            "single-origin pour-over manual brew hand brew specialty coffee cortado thoughtful flavors",
            ["pour-over", "espresso", "quality", "ambience"],
        )

    if not facts:
        add_fact(
            facts,
            "General user info provided.",
            "general_user_context",
            text[:500] or "general cafe fit",
            ["quality", "seating", "service"],
        )
    return facts


def retrieve_for_facts(facts: list[dict[str, Any]], index: ReviewIndex, per_fact: int = 6) -> list[dict[str, Any]]:
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for fact in facts:
        matches = index.search(fact["query"], fact.get("tags", []), limit=per_fact)
        fact["matchedReviewIds"] = [review["id"] for review in matches]
        for review in matches:
            current = evidence_by_id.get(review["id"])
            if current is None or review.get("score", 0) > current.get("score", 0):
                evidence_by_id[review["id"]] = review
    evidence = list(evidence_by_id.values())
    evidence.sort(key=lambda item: (-item.get("score", 0), -item["rating"], item["id"]))
    return evidence[:18]


def add_conflict_candidates(
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    needed_tags = {tag for fact in facts for tag in fact.get("tags", [])}
    if "wifi" not in needed_tags:
        return evidence
    by_id = {review["id"]: review for review in evidence}
    wifi_reviews = [review for review in reviews if "wifi" in review["tags"]]
    positive = [review for review in wifi_reviews if review_sentiment(review, "wifi") == "positive"]
    negative = [review for review in wifi_reviews if review_sentiment(review, "wifi") == "negative"]
    for bucket in (positive, negative):
        for review in sorted(bucket, key=lambda item: item.get("reviewDate", ""), reverse=True)[:2]:
            by_id.setdefault(review["id"], dict(review, score=0.0))
    merged = list(by_id.values())
    merged.sort(key=lambda item: (-item.get("score", 0), item.get("reviewDate", ""), -item["rating"]), reverse=False)
    merged.sort(key=lambda item: (-item.get("score", 0), item.get("reviewDate", "")), reverse=True)
    return merged[:18]


def review_sentiment(review: dict[str, Any], topic: str) -> str | None:
    text = f"{review.get('body', '')} {' '.join(review.get('tags', []))}".lower()
    if topic == "wifi":
        if contains_any(text, ["upgraded", "held a full video call", "reliable-wifi", "stable"]):
            return "positive"
        if contains_any(text, ["struggled", "dropped", "unreliable", "mixed"]):
            return "negative"
    return None


def detect_conflicts(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notes = []
    for topic in ["wifi"]:
        topic_reviews = [review for review in evidence if review_sentiment(review, topic)]
        sentiments = {review_sentiment(review, topic) for review in topic_reviews}
        if not {"positive", "negative"}.issubset(sentiments):
            continue
        latest = max(topic_reviews, key=lambda review: review.get("reviewDate", ""))
        latest_sentiment = review_sentiment(latest, topic)
        superseded = [
            review["id"]
            for review in topic_reviews
            if review["id"] != latest["id"] and review_sentiment(review, topic) != latest_sentiment
        ]
        notes.append(
            {
                "topic": topic,
                "rule": "latest-review-wins",
                "winnerReviewId": latest["id"],
                "winnerDate": latest.get("reviewDate", ""),
                "winnerSentiment": latest_sentiment,
                "supersededReviewIds": superseded,
                "summary": (
                    f"Conflicting {topic} reviews were found. Using latest review "
                    f"{latest['id']} from {latest.get('reviewDate', '')} over older conflicting evidence."
                ),
            }
        )
    return notes


def tag_counts(reviews: list[dict[str, Any]], limit: int | None = 14) -> list[dict[str, Any]]:
    counts = Counter(tag for review in reviews for tag in review["tags"])
    items = counts.most_common(limit) if limit else counts.most_common()
    return [{"tag": tag, "count": count} for tag, count in items]


def review_themes(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review in reviews:
        for tag in review["tags"]:
            buckets[tag].append(review)
    themes = []
    for tag, items in sorted(buckets.items(), key=lambda pair: len(pair[1]), reverse=True)[:12]:
        avg = sum(item["rating"] for item in items) / len(items)
        themes.append({"tag": tag, "count": len(items), "avgRating": round(avg, 2)})
    return themes


def compact_review(review: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": review["id"],
        "reviewDate": review.get("reviewDate", ""),
        "rating": review["rating"],
        "body": review["body"],
        "tags": review["tags"],
    }
    if "score" in review:
        payload["score"] = review["score"]
    if "retrievalSource" in review:
        payload["retrievalSource"] = review["retrievalSource"]
    return payload


def build_default_wiki(reviews: list[dict[str, Any]]) -> str:
    evidence = [review for review in reviews if review["rating"] >= 4][:5]
    evidence_lines = [
        f"- {review['id']} ({review['rating']}/5): {review['body'][:170]}"
        for review in evidence
    ]
    return f"""# Personalized Wiki for Juniper & Finch Coffee

## Bottom Line
Juniper & Finch is a compact Hayes Valley coffee shop with strong drinks, light breakfast, and a mixed seating story. It is broadly appealing, but timing and access matter.

## Best Fit
This default version uses business details and broad review themes before any user context is applied.

## Food / Drink Match
Official highlights include oat maple latte, sesame matcha, cortado, miso mushroom toast, kimchi egg sandwich, and tahini banana bread.

## Logistics
The shop is near Muni and local foot traffic. It has no dedicated parking lot, and review themes suggest parking can be annoying during busy windows.

## Things To Avoid
Weekend brunch is the main risk window for lines, noise, and scarce seats.

## Suggested Visit Plan
For a neutral first visit, try a weekday early afternoon stop and treat the shop as a short neighborhood cafe rather than an all-day workspace.

## Recommended Actions For You
- Start with a weekday early afternoon visit.
- Use the first visit to validate seating, noise, and order pacing.
- If the visit is time-sensitive, avoid weekend brunch.

## Evidence From Reviews
{os.linesep.join(evidence_lines)}
"""


def build_rule_based_wiki(
    user_text: str,
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> str:
    fact_types = {fact["type"] for fact in facts}
    evidence_tags = Counter(tag for review in evidence for tag in review["tags"])
    evidence_ids = ", ".join(review["id"] for review in evidence[:8]) or "no matched reviews"
    avg_rating = sum(review["rating"] for review in evidence) / len(evidence) if evidence else 0
    best_for = []
    if "work_laptop" in fact_types:
        best_for.append("weekday 60-120 minute work blocks, especially Tue-Thu early afternoon")
    elif "prefers_quiet" in fact_types:
        best_for.append("calmer visits outside weekend brunch and rainy-day indoor rushes")
    if "conversation_meetup" in fact_types:
        best_for.append("low-pressure one-on-one meetups outside brunch rush")
    if "likes_manual_brew" in fact_types:
        best_for.append("a drink-focused stop where single-origin pour-over or a careful cortado can carry the visit")
    if {"likes_matcha", "likes_oat_milk", "diet_dairy_sensitive"} & fact_types:
        best_for.append("a drink-first visit built around sesame matcha or oat milk options")
    if not best_for:
        best_for.append("a short neighborhood coffee stop when timing is flexible")

    food_drink = []
    if "likes_matcha" in fact_types:
        food_drink.append("Sesame matcha is a strong match; review snippets repeatedly mention nutty, not-too-sweet matcha.")
    if "likes_oat_milk" in fact_types or "diet_dairy_sensitive" in fact_types:
        food_drink.append("Oat milk drinks are relevant because reviews praise the oat milk option and the official menu supports dairy-free choices.")
    if "likes_savory_breakfast" in fact_types:
        food_drink.append("For savory food, prioritize miso mushroom toast or kimchi egg sandwich over pastry-only orders.")
    if "likes_manual_brew" in fact_types:
        food_drink.append("For manual or specialty coffee preferences, start with the single-origin pour-over; cortado is the safer espresso fallback.")
    if not food_drink:
        food_drink.append("The broadest safe picks are espresso drinks, sesame matcha, and light breakfast items.")

    logistics = []
    if "transport_no_car" in fact_types:
        logistics.append("No-car access is favorable: the shop is near Muni, walkable stops, and review evidence mentions easy transit/walking.")
    if "transport_has_car" in fact_types:
        logistics.append("Driving is the weak point: no dedicated lot, slow street turnover after 9:30 AM, and several parking-frustration reviews.")
    if "work_laptop" in fact_types:
        logistics.append("Laptop use is plausible but tactical: back rail and early afternoon are better than peak hours; outlets are limited.")
        wifi_conflict = next((note for note in conflicts if note["topic"] == "wifi"), None)
        if wifi_conflict and wifi_conflict["winnerSentiment"] == "positive":
            logistics.append(
                f"Wi-Fi reviews conflict, so the wiki uses the latest review ({wifi_conflict['winnerReviewId']}, {wifi_conflict['winnerDate']}) by default."
            )
    if not logistics:
        logistics.append("Access is easiest by walking, transit, or short rideshare. Parking should not be treated as guaranteed.")

    avoid = []
    wifi_conflict = next((note for note in conflicts if note["topic"] == "wifi"), None)
    if "dislikes_lines" in fact_types or "likes_savory_breakfast" in fact_types:
        avoid.append("Avoid Sat-Sun 10:00 AM - 1:00 PM if long lines or brunch noise would ruin the visit.")
    if "prefers_quiet" in fact_types:
        avoid.append("Avoid rainy crowded afternoons and weekend chatter if the goal is a calm room.")
    if "work_laptop" in fact_types:
        if wifi_conflict and wifi_conflict["winnerSentiment"] == "positive":
            avoid.append("For mission-critical calls, keep a backup even though the latest Wi-Fi review is positive.")
        else:
            avoid.append("Avoid assuming reliable video-call conditions; reviews call Wi-Fi mixed for calls.")
    if "diet_dairy_sensitive" in fact_types:
        avoid.append("Avoid pastry-only decisions without checking ingredients; choose clearly dairy-free drinks when possible.")
    if not avoid:
        avoid.append("Avoid peak brunch if seating matters.")

    suggested_plan = build_visit_plan(fact_types)
    recommended_actions = build_recommended_actions(fact_types)
    evidence_lines = [
        f"- {review['id']} ({review.get('reviewDate', '')}, {review['rating']}/5, {', '.join(review['tags'][:5])}): {review['body']}"
        for review in evidence[:8]
    ]
    conflict_lines = [f"- {note['summary']} Superseded: {', '.join(note['supersededReviewIds']) or 'none'}." for note in conflicts]
    if not conflict_lines:
        conflict_lines = ["- No direct review conflicts were detected in this retrieval set."]
    top_theme_lines = [
        f"- {tag}: {count} matched mentions"
        for tag, count in evidence_tags.most_common(6)
    ]

    return f"""# Personalized Wiki for Juniper & Finch Coffee

## Bottom Line
Juniper & Finch works best as a timed, preference-aware stop rather than a generic cafe pick. The retrieved review set averages {avg_rating:.1f}/5 across {len(evidence)} matched reviews ({evidence_ids}).

## Best Fit
{bullet_list(best_for)}

## Food / Drink Match
{bullet_list(food_drink)}

## Logistics
{bullet_list(logistics)}

## Things To Avoid
{bullet_list(avoid)}

## Suggested Visit Plan
{suggested_plan}

## Recommended Actions For You
{bullet_list(recommended_actions)}

## Evidence From Reviews
{os.linesep.join(evidence_lines)}

Conflict handling:
{os.linesep.join(conflict_lines)}

## Matched Review Themes
{os.linesep.join(top_theme_lines)}
"""


def build_visit_plan(fact_types: set[str]) -> str:
    time = "Tuesday-Thursday around 2:00 PM"
    order = "sesame matcha or oat maple latte"
    seat = "back rail or a window seat"
    duration = "60-90 minute"
    if "likes_savory_breakfast" in fact_types:
        order += " plus miso mushroom toast or kimchi egg sandwich"
    if "likes_manual_brew" in fact_types:
        order = "single-origin pour-over first, with a cortado as the espresso fallback"
        seat = "window seat or a small table where conversation is easy"
    if "conversation_meetup" in fact_types:
        time = "weekday early afternoon or a quieter weekend late afternoon"
    if "work_laptop" in fact_types:
        seat = "back rail first, then window seat if the rail is full"
        duration = "60-120 minute"
    if "transport_has_car" in fact_types:
        time = "weekday early afternoon after checking parking"
    if "dislikes_lines" in fact_types:
        time = "weekday early afternoon, not weekend brunch"
    suffix = ", with a backup plan for calls" if "work_laptop" in fact_types else ""
    return f"Go {time}. Order {order}. Aim for the {seat}. Treat it as a {duration} visit{suffix}."


def build_recommended_actions(fact_types: set[str]) -> list[str]:
    actions = []
    if "transport_no_car" in fact_types:
        actions.append("Take Muni or walk from the closest Hayes Valley stop instead of planning around parking.")
    if "transport_has_car" in fact_types:
        actions.append("Check parking before leaving and keep the paid garage as a backup, not the default.")
    if "dislikes_lines" in fact_types:
        actions.append("Skip Sat-Sun 10:00 AM - 1:00 PM and use weekday afternoon as the first test window.")
    if "likes_manual_brew" in fact_types:
        actions.append("Order the single-origin pour-over first; use cortado as the fallback if pour-over is unavailable.")
    if "conversation_meetup" in fact_types:
        actions.append("Pick a window seat or small table and avoid the busiest brunch window for easier conversation.")
    if "likes_savory_breakfast" in fact_types:
        actions.append("Pair the drink with miso mushroom toast or kimchi egg sandwich rather than pastry-only items.")
    if "work_laptop" in fact_types:
        actions.append("Choose the back rail first, confirm outlet availability, and keep a backup for video calls.")
    if "diet_dairy_sensitive" in fact_types:
        actions.append("Default to oat milk or clearly dairy-free options before trying pastries.")
    if not actions:
        actions.append("Try a short weekday afternoon visit before committing to a longer stay.")
        actions.append("Use the first visit to test seating, line speed, and drink quality.")
    return actions[:5]


def extract_display_name(user_text: str) -> str:
    match = re.search(r"(?:Name:|name is|I am|I'm|called)\s*([A-Za-z][A-Za-z\s'-]{1,48})", user_text)
    if not match:
        return "This User"
    return " ".join(part.capitalize() for part in match.group(1).split()[:4])


def try_llm_wiki(
    user_text: str,
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> tuple[str | None, dict[str, str]]:
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None, {"mode": "llm-required", "detail": "No LLM_API_KEY or OPENAI_API_KEY is set. Personalized wiki generation requires a stateless LLM call."}
    endpoint = os.environ.get("LLM_ENDPOINT", "https://api.openai.com/v1/chat/completions")
    model = os.environ.get("LLM_MODEL", "gpt-5.5")
    reasoning_effort = (
        os.environ.get("LLM_REASONING_EFFORT")
        or os.environ.get("OPENAI_REASONING_EFFORT")
        or "low"
    ).strip()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a stateless wiki generator. You have no memory and must not preserve facts from previous generations. "
                    "Generate a concise personalized Markdown wiki for a local business. "
                    "The current userText is the only source of user preferences. If a preference is absent from userText, do not include recommendations based on it, even if it appeared in a previous generation. "
                    "Do not infer cafe work needs from occupation descriptions like 'works between client calls'; only include laptop/work-block/video-call advice when the current userText explicitly asks for laptop, remote work, work blocks, Wi-Fi for calls, or working from the cafe. "
                    "Use exactly these sections: Bottom Line, Best Fit, Food / Drink Match, "
                    "Logistics, Things To Avoid, Suggested Visit Plan, Recommended Actions For You, Evidence From Reviews. "
                    "If a retrieved review is tagged user-added or is the newest relevant review, reflect its concrete information explicitly and cite its review ID. "
                    "Cite review IDs for claims. Do not address the user by name or say that the page was written for a specific person; let the personalization show through the selected recommendations."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "business": BUSINESS,
                        "surroundings": SURROUNDINGS,
                        "userText": user_text,
                        "facts": facts,
                        "evidence": [compact_review(review) for review in evidence[:12]],
                        "conflictPolicy": "When review information conflicts, use the newest review by reviewDate and mention superseded older evidence.",
                        "conflicts": conflicts,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    if reasoning_effort and reasoning_effort.lower() != "default":
        payload["reasoning_effort"] = reasoning_effort
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return None, {"mode": "llm-error", "detail": f"LLM call failed. No rule-based personalized wiki was generated: HTTP {exc.code}: {detail[:600]}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as exc:
        return None, {"mode": "llm-error", "detail": f"LLM call failed. No rule-based personalized wiki was generated: {exc}"}
    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        return None, {"mode": "llm-error", "detail": f"LLM response was not usable. No rule-based personalized wiki was generated: {exc}"}
    effort_detail = reasoning_effort if reasoning_effort and reasoning_effort.lower() != "default" else "provider default"
    return content, {"mode": "llm-stateless", "detail": f"Generated through a stateless OpenAI-compatible call using {model}; reasoning_effort={effort_detail}."}


def build_model_required_wiki(status: dict[str, str]) -> str:
    return f"""# Personalized Wiki for Juniper & Finch Coffee

## LLM Required
Personalized wiki generation is configured to use a stateless LLM call only. No rule-based personalized wiki was generated.

## Current Status
{status.get('detail', 'Model is unavailable.')}

## How To Enable
- Set `LLM_API_KEY` or `OPENAI_API_KEY`.
- Optional: set `LLM_MODEL`; default is `gpt-5.5`.
- Optional: set `LLM_REASONING_EFFORT`; default is `low` for lower latency.
- Click `Generate Wiki` again after the key is available.
"""


def write_wiki_artifacts(
    current_wiki: str,
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]] | None = None,
) -> None:
    if WIKI_DIR.exists():
        shutil.rmtree(WIKI_DIR)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    write_text(WIKI_DIR / "current-personalized-wiki.md", current_wiki)
    write_text(
        WIKI_DIR / "business-context.md",
        f"""# Business Context

## Official Claims
{bullet_list(BUSINESS['officialClaims'])}

## Menu Highlights
{bullet_list(BUSINESS['menuHighlights'])}

## Surroundings
{bullet_list(SURROUNDINGS['transit'] + SURROUNDINGS['parking'] + SURROUNDINGS['localRhythm'])}
""",
    )


def cognee_memory_status(last_error: str | None = None) -> dict[str, str]:
    try:
        import cognee  # noqa: F401
    except Exception as exc:
        return {
            "mode": "not-installed",
            "detail": f"Cognee is not importable, so generated wiki memory stays in local markdown/state files. {last_error or exc}",
        }
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        return {
            "mode": "needs-llm-api-key",
            "detail": "Cognee is available, but LLM_API_KEY or OPENAI_API_KEY is not set; local markdown/state files are used for this run.",
        }
    return {
        "mode": "ready",
        "detail": f"Cognee can ingest generated wiki memory into dataset '{COGNEE_DATASET}'.",
    }


def read_last_cognee_status() -> dict[str, str] | None:
    path = cognee_status_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(payload, dict) and "mode" in payload and "detail" in payload:
        return {"mode": str(payload["mode"]), "detail": str(payload["detail"])}
    return None


def write_last_cognee_status(status: dict[str, str]) -> None:
    write_json(cognee_status_path(), {**status, "updatedAt": now_stamp()})


def update_state_cognee_status(status: dict[str, str]) -> None:
    try:
        state = read_json(STATE_FILE, default_state())
        state["memoryStatus"] = status
        state["updatedAt"] = now_stamp()
        write_json(STATE_FILE, state)
    except Exception:
        pass


def build_cognee_memory_documents(
    wiki: str,
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    session_id: str,
) -> list[str]:
    return [
        f"""# Generated Personalized Business Wiki

Business: {BUSINESS['name']}
Dataset: {COGNEE_DATASET}
Redis session memory: {REDIS_SESSION_PREFIX}{session_id}
Generated: {now_stamp()}

{wiki}
""",
        f"""# Parsed User Facts

{json.dumps(facts, ensure_ascii=False, indent=2)}
""",
        f"""# Review Evidence Used For Wiki Generation

{json.dumps([compact_review(review) for review in evidence], ensure_ascii=False, indent=2)}
""",
        f"""# Conflict Resolution Notes

Policy: latest review wins when review evidence conflicts.

{json.dumps(conflicts, ensure_ascii=False, indent=2)}
""",
    ]


async def remember_with_cognee_async(
    wiki: str,
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    session_id: str,
) -> dict[str, str]:
    try:
        import cognee
    except Exception as exc:
        return cognee_memory_status(str(exc))
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        return cognee_memory_status()
    ensure_openai_key_alias()
    documents = build_cognee_memory_documents(wiki, facts, evidence, conflicts, session_id)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if os.environ.get("COGNEE_PRUNE_BEFORE_INGEST", "1").lower() not in {"0", "false", "no"}:
                await cognee.prune.prune_data()
                await cognee.prune.prune_system(graph=True, vector=True, metadata=True)
            await cognee.add(documents, dataset_name=COGNEE_DATASET)
            await cognee.cognify(datasets=[COGNEE_DATASET])
    except Exception as exc:
        return {
            "mode": "error",
            "detail": f"Cognee ingestion failed; local markdown/state files still contain the generated wiki. {exc}",
        }
    return {
        "mode": "cognee",
        "detail": f"Distilled Redis session memory key {REDIS_SESSION_PREFIX}{session_id}, wiki, parsed facts, evidence, and conflict notes into Cognee dataset '{COGNEE_DATASET}' and ran cognify.",
    }


def remember_with_cognee(
    wiki: str,
    facts: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    session_id: str,
) -> dict[str, str]:
    if os.environ.get("COGNEE_RUN_MODE", "background").lower() not in {"sync", "synchronous"}:
        ready_status = cognee_memory_status()
        if ready_status["mode"] != "ready":
            return ready_status
        queued_status = {
            "mode": "cognee-queued",
            "detail": f"Queued Redis session memory key {REDIS_SESSION_PREFIX}{session_id}, wiki, parsed facts, evidence, and conflict notes for Cognee dataset '{COGNEE_DATASET}'. Refresh after a moment to see completion.",
        }
        write_last_cognee_status(queued_status)

        def run_background_cognee() -> None:
            try:
                status = asyncio.run(remember_with_cognee_async(wiki, facts, evidence, conflicts, session_id))
            except Exception as exc:
                status = {
                    "mode": "error",
                    "detail": f"Cognee background ingestion failed; local markdown/state files still contain the generated wiki. {exc}",
                }
            write_last_cognee_status(status)
            update_state_cognee_status(status)

        threading.Thread(target=run_background_cognee, daemon=True).start()
        return queued_status

    try:
        status = asyncio.run(remember_with_cognee_async(wiki, facts, evidence, conflicts, session_id))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            status = loop.run_until_complete(remember_with_cognee_async(wiki, facts, evidence, conflicts, session_id))
        finally:
            loop.close()
    write_last_cognee_status(status)
    return status
    write_text(
        WIKI_DIR / "retrieval-evidence.md",
        f"""# Retrieval Evidence

## Parsed Facts
{json.dumps(facts, ensure_ascii=False, indent=2)}

## Matched Reviews
{json.dumps([compact_review(review) for review in evidence], ensure_ascii=False, indent=2)}

## Conflict Notes
{json.dumps(conflicts or [], ensure_ascii=False, indent=2)}
""",
    )


def default_state() -> dict[str, Any]:
    reviews = load_reviews()
    wiki = build_default_wiki(reviews)
    return {
        "userText": DEFAULT_USER_TEXT,
        "wiki": wiki,
        "facts": [],
        "evidence": [],
        "conflicts": [],
        "indexed": False,
        "indexStatus": {"mode": "not-indexed", "detail": "Review index has not been built yet."},
        "memoryStatus": cognee_memory_status(),
        "sessionMemoryStatus": {"mode": "not-started", "detail": "No generation turn has been written to Redis session memory yet."},
        "generatorStatus": {"mode": "default", "detail": "Default wiki uses business info and broad review themes."},
        "createdAt": now_stamp(),
        "updatedAt": now_stamp(),
    }


def reset_demo() -> dict[str, Any]:
    ensure_dirs()
    reviews = generate_reviews()
    write_json(DATA_DIR / "reviews.json", reviews)
    state = default_state()
    write_json(STATE_FILE, state)
    write_wiki_artifacts(state["wiki"], [], [], [])
    return get_state()


def build_index() -> dict[str, Any]:
    ensure_dirs()
    reviews = load_reviews()
    review_index = ReviewIndex(reviews)
    index_status = review_index.index()
    state = read_json(STATE_FILE, default_state())
    state["indexed"] = True
    state["indexStatus"] = index_status
    state["memoryStatus"] = cognee_memory_status()
    state["updatedAt"] = now_stamp()
    write_json(STATE_FILE, state)
    return get_state()


def generation_session_id(user_text: str) -> str:
    digest = hashlib.sha1(user_text.strip().encode("utf-8")).hexdigest()[:12]
    return f"generation-{digest}"


def generate_personalized_wiki(user_text: str) -> dict[str, Any]:
    reviews = load_reviews()
    index = ReviewIndex(reviews)
    index_status = index.index()
    facts = parse_user_facts(user_text)
    evidence = retrieve_for_facts(facts, index)
    evidence = add_conflict_candidates(facts, evidence, reviews)
    conflicts = detect_conflicts(evidence)
    llm_wiki, generator_status = try_llm_wiki(user_text, facts, evidence, conflicts)
    wiki = llm_wiki or build_model_required_wiki(generator_status)
    session_id = generation_session_id(user_text)
    session_status = index.write_session_memory(session_id, user_text, facts, evidence, conflicts, wiki)
    memory_status = remember_with_cognee(wiki, facts, evidence, conflicts, session_id)
    state = read_json(STATE_FILE, default_state())
    state.update(
        {
            "userText": user_text,
            "wiki": wiki,
            "facts": facts,
            "evidence": [compact_review(review) for review in evidence],
            "conflicts": conflicts,
            "indexed": True,
            "indexStatus": index_status,
            "memoryStatus": memory_status,
            "sessionMemoryStatus": session_status,
            "generatorStatus": generator_status,
            "updatedAt": now_stamp(),
        }
    )
    write_json(STATE_FILE, state)
    write_wiki_artifacts(wiki, facts, evidence, conflicts)
    return {
        "wiki": wiki,
        "facts": facts,
        "evidence": [compact_review(review) for review in evidence],
        "conflicts": conflicts,
        "status": {
            "index": index_status,
            "memory": memory_status,
            "sessionMemory": session_status,
            "generator": generator_status,
        },
        **get_state(),
    }


def list_wiki_files() -> list[dict[str, str]]:
    if not WIKI_DIR.exists():
        return []
    files = []
    for path in sorted(WIKI_DIR.rglob("*.md")):
        files.append({"path": str(path.relative_to(WIKI_DIR)).replace(os.sep, "/"), "content": path.read_text(encoding="utf-8")})
    return files


def get_state() -> dict[str, Any]:
    ensure_dirs()
    if not STATE_FILE.exists():
        reset_demo()
    state = read_json(STATE_FILE, default_state())
    reviews = load_reviews()
    return {
        **state,
        "defaultWiki": build_default_wiki(reviews),
        "business": BUSINESS,
        "surroundings": SURROUNDINGS,
        "reviewCount": len(reviews),
        "tagCounts": tag_counts(reviews),
        "reviewThemes": review_themes(reviews),
        "sampleReviews": [compact_review(review) for review in reviews[:6]],
        "wikiFiles": list_wiki_files(),
    }


def get_reviews_payload() -> dict[str, Any]:
    reviews = load_reviews()
    return {
        "business": BUSINESS,
        "reviewCount": len(reviews),
        "tagCounts": tag_counts(reviews, None),
        "reviews": [compact_review(review) for review in reviews],
    }


class DemoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            self.send_json(get_state())
            return
        if parsed.path == "/api/reviews":
            self.send_json(get_reviews_payload())
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/reset":
                self.send_json(reset_demo())
            elif parsed.path == "/api/index":
                self.send_json(build_index())
            elif parsed.path == "/api/reviews":
                self.send_json(add_review(self.read_body()))
            elif parsed.path == "/api/wiki/generate":
                payload = self.read_body()
                self.send_json(generate_personalized_wiki(payload.get("userText", DEFAULT_USER_TEXT)))
            else:
                self.send_json({"error": "Unknown endpoint"}, status=404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=500)

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def serve_static(self, request_path: str) -> None:
        if request_path == "/review":
            path = "review.html"
        else:
            path = unquote(request_path).lstrip("/") or "index.html"
        target = (PUBLIC_DIR / path).resolve()
        if not str(target).startswith(str(PUBLIC_DIR.resolve())) or not target.exists() or target.is_dir():
            self.send_json({"error": "Not found"}, status=404)
            return
        content_type = "text/plain; charset=utf-8"
        if target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.end_headers()
        self.wfile.write(target.read_bytes())

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_server(port: int = DEFAULT_PORT) -> None:
    reset_demo()
    server = ThreadingHTTPServer(("127.0.0.1", port), DemoHandler)
    print(f"Personal Business Wiki demo running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_server(int(os.environ.get("PORT", DEFAULT_PORT)))
