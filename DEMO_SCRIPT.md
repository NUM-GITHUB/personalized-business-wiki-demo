# 3-Minute Demo Script

## Goal

Show that the wiki is not static: it changes when the user's facts change, and it also changes when the review corpus changes.

## Setup

```bash
python3 -m pip install --user -r requirements.txt
redis-server --daemonize yes
export REDIS_URL=redis://localhost:6379/0
export LLM_API_KEY="<event-provided-key>"
export LLM_MODEL=gpt-5.5
export LLM_REASONING_EFFORT=low
python3 server.py
```

Open:

```text
http://127.0.0.1:8891
http://127.0.0.1:8891/review
```

## Pitch Track

### 1. Problem

Small businesses have hundreds of useful reviews, but their website or default profile is generic. A user with no car, dietary constraints, line sensitivity, or a specific date plan needs a different wiki than a generic visitor.

### 2. Baseline Wiki

Open `/`.

Say:

```text
The only editable input is User Info. The default wiki is generic business info; the generated wiki is personalized from the current user text and current review evidence.
```

Click `Generate Wiki`.

Point out:

- Redis review retrieval status
- Redis session memory status
- Cognee memory status
- generated wiki sections
- parsed facts and evidence cards

### 3. Add User Fact

Add this line to User Info:

```text
She wants to meet her boyfriend who prefers special manual brew coffee.
```

Click `Generate Wiki`.

Expected result:

- wiki mentions manual brew, pour-over, or coffee-focused conversation
- suggested plan shifts toward a low-pressure one-on-one meetup

### 4. Delete User Fact

Delete this line if present:

```text
She often carries a laptop and wants a calm place for 60-120 minute work blocks.
```

Click `Generate Wiki`.

Expected result:

- no "60-120 minute work blocks"
- no laptop/outlet/work-session recommendation
- remaining no-car, matcha/oat milk, and line-avoidance logic stays

### 5. Add Review

Open `/review`.

Add:

```text
The Panama Gesha manual-brew flight was the best thing on the menu today. The slow bar seat was quiet enough for a first date conversation, and the barista explained tasting notes without rushing us. No line at 2:30 PM.
```

Use:

```text
rating: 5
reviewDate: 2026-05-16
tags: manual gesha quiet date no-line
```

Submit, then return to `/` and click `Generate Wiki` with the same manual-brew user info.

Expected result:

- evidence includes the newly added review id
- wiki mentions Gesha/manual brew or the new no-line afternoon evidence

### 6. Close

Say:

```text
Redis is the fast working memory: reviews and the current generation turn live there. Cognee is the permanent memory: generated wiki, facts, evidence, and conflict notes get distilled into the graph. The LLM is stateless, so changing current user text or current reviews changes the wiki instead of relying on hidden memory.
```

## What To Mention If Asked

- If RediSearch is available, the app uses it. If not, it still uses Redis hashes and a local scorer over Redis-backed records.
- If the LLM key is missing, the app shows an `LLM Required` page instead of pretending with a rule-based personalized wiki.
- Conflict policy is latest-review-wins when evidence disagrees.
