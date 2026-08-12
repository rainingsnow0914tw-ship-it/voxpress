# Personalization and local lexicon learning

## Boundary

The public repository contains the personalization mechanism, schemas, safety rules, and synthetic tests. A user's real vocabulary and correction history stay in user-scoped files and must never be committed, logged, uploaded in diagnostics, or copied into public fixtures.

Current local inputs:

- `vocab.json`: terms that help Whisper recognize likely words before transcription;
- `corrections.json`: bounded post-transcription phrase corrections;
- recent in-memory context: a short rolling hint that is not persisted by the core runtime;
- optional `prefix` and `prefix_windows`: a visible text convention configured locally.

The receiving application sees a prefix such as `🎤 ` as ordinary Unicode text. It can help an AI infer that homophone errors are more likely, but it is not trusted metadata and does not prove that audio was used. The receiving workflow should explicitly define the convention.

## Receiving-AI convention

Tell each receiving AI what the marker means before enabling it. A reusable instruction is:

> When my message begins with `🎤`, the text came from speech recognition; you do not have the original audio. Interpret likely homophone errors, punctuation noise, and spoken fillers by pronunciation and context first. Treat an unfamiliar odd term as a possible recognition error rather than inventing a new term. If multiple readings remain plausible or the sentence still does not fit the context, ask me one short clarifying question instead of guessing. Exception: preserve code, URLs, and text explicitly identified as quoted or copied exactly as written.

Examples such as `模式密碼` → `摩斯密碼` or `Bispy` → `Bixby` illustrate phonetic interpretation; they are not automatic global replacement rules.

Enable the marker only for selected receiving windows when appropriate:

```toml
prefix = "🎤 "
prefix_windows = "Assistant A,Assistant B"
```

`prefix_windows` is a case-insensitive comma-separated list of window-title substrings. It is not application authentication: titles can change, collide, or be imitated. If it is empty while `prefix` is non-empty, every target receives the marker. Keep the real local assistant/window list private.

The marker changes interpretation, never authority. Anyone can type `🎤`; it must not weaken security checks, grant tool permissions, or prove provenance.

## Planned no-hand-edit learning loop

“Automatic” should mean that the user never has to open and edit JSON. It should not mean that an uncertain model guess silently becomes a global replacement.

```text
dictated message
  -> receiving AI notices a likely term mismatch
  -> high confidence: propose the intended term
     low confidence: ask one short clarification
  -> user confirmation becomes a minimal local evidence record
  -> conversation close emits a structured lexicon review
  -> local validator checks collisions and chooses a safe destination
  -> confirmed safe update is applied atomically
```

The conversation host adapter is separate from the real-time VoxPress engine. It may read only sources the user has explicitly enabled, and it writes into a private local learning directory such as `%USERPROFILE%\.voxpress\learning\`.

## Candidate record

A public schema can use synthetic data like this:

```json
{
  "heard": "興和引擎",
  "intended": "星河引擎",
  "evidence": "user_confirmed",
  "scope": "phrase",
  "occurrences": 2,
  "source_ref": "local-hash-only",
  "status": "candidate"
}
```

Do not store an entire conversation when a short confirmed pair is sufficient. A source reference should be a local opaque identifier or hash, not a public URL or transcript.

## Classification rules

| Candidate shape | Destination | Required gate |
| --- | --- | --- |
| Important term that Whisper should expect | vocabulary | confirmed spelling; prompt-budget check |
| Stable multi-character mishearing | phrase correction | confirmation plus collision tests |
| One-character name or ambiguous common word | contextual rule | never a global replacement; position/context tests |
| Weak inference with no confirmation | candidate inbox | ask the user or leave pending |
| One-off slip or user misspeaking | no update | record nothing |

The system should prefer a vocabulary hint when it can prevent the error before transcription. Exact post-corrections are appropriate only when their false-positive surface is bounded. Single-character global replacements are prohibited because they can corrupt unrelated words.

## Conversation-close review

At the end of an AI conversation, the host adapter should produce a private review table with:

- what was heard;
- the confirmed intended term;
- why the system believes they correspond;
- whether the user confirmed it;
- proposed destination (`vocabulary`, `phrase correction`, `context rule`, or `ignore`);
- collision-test result;
- applied, pending, or rejected status.

The review is an audit trail, not public project data. It gives the user a way to correct the learner without hand-maintaining its storage format.

## Safety and privacy requirements

- No raw audio or full transcript is required for a confirmed phrase pair.
- No candidate becomes active solely because a language model inferred it.
- User confirmation may happen naturally in conversation; the workflow should capture it without asking the same question again.
- Writes are atomic and retain a last-known-good version.
- Every change is reversible and records which candidate caused it.
- Public tests use invented terms only.
- Custom vocabulary/correction paths must stay outside source checkouts; Git ignore rules cannot protect every user-chosen filename.
- The learning job never edits a live dictionary while its validation suite is failing.

This learning loop is a planned extension point, not a claim that v0.2 already reads AI conversations or autonomously changes a user's dictionary.
