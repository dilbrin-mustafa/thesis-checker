# 02 — Security, Privacy & Guardrails

Two distinct problem classes, both graded:

- **Security & privacy** — the system handles unpublished, confidential academic work. Under GDPR this is personal data.
- **Ethical guardrails** — the system makes claims about plagiarism and AI authorship. Those claims can damage a person. This is not optional polish; it is a core design constraint and a thesis chapter.

---

## PART A — SECURITY

## 1. Threat model (STRIDE, abbreviated)

| Threat | Scenario | Control |
|---|---|---|
| **Spoofing** | Attacker accesses another student's thesis | Per-user auth; every DB query scoped by `owner_id`; unguessable IDs (UUIDv4, never sequential) |
| **Tampering** | Malicious DOCX/PDF/TEX exploits a parser | Sandboxed parsing, resource limits, no shell-out, XML hardening (§3) |
| **Repudiation** | Dispute over what the system reported | Immutable audit log; reports carry a content hash + timestamp + engine version |
| **Information disclosure** | Draft thesis leaks to a third-party API or another user | Local-first default; opt-in external calls; strict tenant isolation; short retention |
| **DoS** | 400-page adversarial PDF ("zip bomb" equivalent) exhausts the host | Size/page caps, per-stage timeouts, memory ceiling, per-user quota |
| **Elevation of privilege** | LaTeX `\write18`, container escape | Never invoke a TeX engine; non-root container; read-only FS; dropped capabilities |

**Highest-severity realistic risk: an unpublished thesis being transmitted to a third-party service without the author's informed consent.** Design against this one first.

## 2. Authentication & authorisation

- **Full deployment:** OIDC against PG's identity provider if obtainable; otherwise email+password with Argon2id, or GitHub/Google OAuth.
- **Demo deployment:** anonymous sessions with a signed, short-lived cookie; no accounts; hard quotas per IP. Simpler and safer than half-built auth.
- **Authorisation:** single rule, enforced at the repository layer, never in route handlers — *a user may only touch rows where `owner_id = current_user.id`*. Enforcing it in one place is the difference between a provably correct system and a game of whack-a-mole.
- **Secrets:** environment variables only. Never in the repo, never in client-side code, never in logs. On HF Spaces use Settings → Secrets. Add `gitleaks` to CI — a public Space repo makes a leaked key an immediate incident.

## 3. File upload security — the primary attack surface

Document parsers are historically a rich source of CVEs, and you are accepting three of the worst formats from untrusted users.

**Validation pipeline, in order (fail closed at every step):**

1. **Size cap** before reading — 50 MB default, streamed to disk, never `await file.read()` into memory.
2. **Magic-byte sniffing** (`python-magic`) — never trust the extension or the client-supplied MIME type.
3. **Extension ↔ content agreement.**
4. **Structural pre-flight** — page count, DOCX part count, LaTeX file count against caps.
5. **Filename sanitisation** — discard the client name entirely; store as `{uuid}.{ext}`. Eliminates path traversal and Unicode-homoglyph tricks in one move.
6. **Optional AV scan** (ClamAV) in the full deployment.

**Format-specific hardening:**

| Format | Risk | Control |
|---|---|---|
| DOCX | XXE, billion-laughs, zip bomb | `defusedxml` for **all** XML; cap uncompressed size and compression ratio (reject > 100:1); cap member count; reject absolute/`..` paths in the zip |
| PDF | Embedded JS, external references, decompression bombs, parser CVEs | Never execute; disable external resource loading; page cap (500); pin and patch PyMuPDF |
| LaTeX | **`\write18` shell escape, `\input{/etc/passwd}`** | **Never run a TeX engine.** Parse as text only. Reject/ignore `\input`/`\include` outside the upload root; cap include depth |
| All | Decompression bomb | Enforce a total-extracted-bytes budget, not just an archive size |

**Sandboxing.** Run parsing in a separate process with `RLIMIT_AS`, `RLIMIT_CPU`, and a wall-clock timeout, so a hostile document kills a worker rather than the service. In the full deployment, give the parser its own container: non-root, read-only root filesystem, `--cap-drop=ALL`, no network namespace. A parser process has no legitimate reason to make an outbound connection — enforce that structurally rather than by policy.

```python
# Illustrative shape of the parse boundary
def parse_sandboxed(path: Path, fmt: str, timeout_s: int = 120) -> DocumentIR:
    with ProcessPoolExecutor(max_workers=1, initializer=_apply_rlimits) as pool:
        try:
            return pool.submit(_parse, path, fmt).result(timeout=timeout_s)
        except TimeoutError:
            raise ParseError("Document exceeded the processing time limit")
        except MemoryError:
            raise ParseError("Document exceeded the memory limit")
```

## 4. Data protection & GDPR

A thesis contains the author's name, supervisor's name, institutional affiliation, and unpublished intellectual work. GDPR applies. For an MSc at a Polish university this is genuinely examinable material — treat it as a feature, not paperwork.

**Data inventory**

| Data | Category | Retention | Basis |
|---|---|---|---|
| Uploaded file | Personal + confidential | **24 h default**, then hard delete | Consent |
| Document IR (text) | Personal + confidential | Life of the analysis, max 30 days | Consent |
| Findings / report | Personal | 30 days, or until user deletes | Consent |
| Embeddings | Pseudonymised (partly invertible — treat as personal) | With the analysis | Consent |
| Audit log | Pseudonymised | 12 months | Legitimate interest |
| Research corpus (study) | Personal | Per the consent form | Explicit written consent |

**Principles to implement, not just document:**
- **Data minimisation** — do not persist the original file after the IR is built, unless the user asked for source highlighting (then keep only the pages needed).
- **Purpose limitation** — an uploaded thesis is **never** added to the plagiarism corpus. This is the trust-destroying mistake commercial tools are criticised for. Make it an explicit, prominent product promise.
- **Right to erasure** — a working "Delete everything" button that cascades to blobs, DB rows, caches, and derived artefacts. Test it.
- **Encryption** — TLS in transit; at rest via disk encryption plus application-level encryption of stored document text in the full deployment.
- **Transparency** — a plain-language processing notice at upload: what is analysed, what leaves the machine, how long it is kept.

**External-call consent gate.** Any egress carrying user text requires an explicit, per-analysis, opt-in toggle, and the UI must name the recipient:

```
☐ Check for online sources        → sends selected sentences to Crossref, OpenAlex, and a web search provider
☐ Advanced language analysis      → sends section text to <named LLM provider>
☑ Verify citations                → sends only reference metadata (titles, DOIs), never thesis body text
```

Default: everything that transmits body text is **off**. Log every external call (endpoint, byte count, timestamp) and expose that log to the user. This single feature is a genuine differentiator over commercial tools and makes a strong thesis subsection.

## 5. Application security baseline

| Control | Implementation |
|---|---|
| Input validation | Pydantic models on every endpoint; strict types; no `dict[str, Any]` at boundaries |
| SQL injection | SQLAlchemy parameterised queries only; zero string-built SQL |
| SSRF | **Critical** — citation link-checking fetches user-controlled URLs. Allowlist schemes (http/https), resolve DNS and block private/link-local/loopback ranges *before* connecting, disable redirects to new hosts, set tight timeouts, cap response size |
| XSS | React escapes by default; **never** `dangerouslySetInnerHTML` for extracted thesis text; sanitise with DOMPurify if HTML rendering is unavoidable |
| CSRF | `SameSite=Strict` cookies, or bearer tokens in headers |
| Headers | CSP (no `unsafe-inline`), HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` |
| Rate limiting | Per-IP and per-user, on upload and analysis specifically (they are the expensive operations) |
| Dependencies | `pip-audit` + Dependabot in CI; pin with a lockfile |
| Secrets scanning | `gitleaks` in CI |
| Error handling | Generic message to the client, full detail to the server log. Never leak stack traces or filesystem paths |
| Logging | **Never log document text.** Log doc IDs and hashes only. Easy to get wrong in debug logging — add a CI check |

## 6. Resource guardrails

| Limit | Default | Rationale |
|---|---|---|
| Upload size | 50 MB | Well above a real thesis |
| Page count | 500 | Above the 60–100 norm with margin |
| Word count | 200 000 | Sanity bound |
| Parse timeout | 120 s | Kills pathological PDFs |
| Per-module timeout | 300 s | Partial results still returned |
| Total analysis timeout | 20 min | Beyond this, something is wrong |
| Concurrent analyses / user | 1 (demo), 3 (full) | Fairness |
| Analyses / user / day | 5 (demo), 50 (full) | Cost control |
| External queries / document | 200 hard cap | Budget control |
| Worker memory | 2 GB RSS | OOM kills a worker, not the host |

**Every module must degrade gracefully.** If similarity times out, the report ships with the other four sections and an honest "this module did not complete" panel. A partial report is useful; a failed job is not.

---

## PART B — ETHICAL GUARDRAILS

## 7. The core principle

> **The system produces evidence for a human decision. It never produces a verdict.**

Every UI string, API field, and report sentence must survive this test. Terms like "plagiarism detected", "AI-written", "cheating" must not exist in the codebase — enforce it with a CI test over the i18n string files. It sounds gimmicky; it is genuinely the most effective way to keep the discipline as the project grows.

## 8. Language rules

| Never | Always |
|---|---|
| "Plagiarism detected" | "Text similar to an external source — review and verify attribution" |
| "This text was written by AI" | "Statistical patterns in this section resemble machine-generated text (confidence: medium). This is not proof." |
| "Fake citation" | "This reference could not be resolved in Crossref/OpenAlex — please verify" |
| "Score: 87/100 — FAIL" | "3 blockers, 12 errors — see the prioritised fix list" |

Both PL and EN strings must be reviewed against this table. Polish phrasing needs care: *"wykryto plagiat"* is an accusation with real consequences; *"wykryto podobieństwo do źródła zewnętrznego"* is a finding.

## 9. AI-detection-specific guardrails

This module carries the highest harm potential and the weakest scientific foundation. Constrain it hard:

1. **Minimum length** — no output below ~150 words per segment. Short-text detection is noise; emitting it is misleading.
2. **Calibrated bands, not numbers** — show "low / moderate / elevated" with an explicit confidence interval, not "87.3% AI".
3. **Mandatory context panel** — always displayed alongside any elevated score: *known false positives on non-native English, on heavily edited text, on formulaic technical writing, and on short passages.*
4. **Never aggregate into the total score.** Reported as a separate, clearly-labelled advisory axis.
5. **Non-native-speaker disclosure** — published research shows detectors disproportionately misclassify non-native English writing. Your users are Polish students writing in English: this is precisely the affected population. Measure it in your evaluation and state it in the UI.
6. **No institutional-reporting feature.** The tool reports to the author, not to the university. Do not build a surveillance workflow.
7. **Right of reply** — a "dispute this finding" affordance that records the author's explanation in the report.

## 10. Plagiarism-specific guardrails

- **Exclusions applied by default** (bibliography, quotations, standard formulae, method boilerplate, the author's own prior work) — with a visible, editable exclusion list so the user can see what was skipped and why.
- **Always show the matched source**, side by side. A similarity percentage with no evidence is unactionable and untrustworthy.
- **Common-phrase suppression** — technical writing legitimately reuses fixed expressions ("as shown in Figure", "the results indicate"). Filter by IDF; do not report boilerplate as similarity.
- **Self-plagiarism is labelled distinctly** from external similarity. Different problem, different remedy.
- **Report a coefficient with a stated definition**, PRP1/PRP2-analogous, not a mystery number.

## 11. Fairness & non-discrimination

| Bias | Mitigation | Measured how |
|---|---|---|
| AIGT penalises non-native English | Report per-group performance; add the UI caveat; prefer PL-fine-tuned models for PL text | Stratified eval: native EN vs PL-author EN |
| Grammar checker penalises non-native phrasing that is not an error | Precision tuning; separate "error" from "style suggestion" | FP rate on published, peer-reviewed non-native text |
| Format rules assume Word | LaTeX users get semantically-equivalent rules, not font rules | Per-format rule applicability matrix |
| Discipline bias in similarity | Field-aware common-phrase filtering | FP rate across faculties |

Put a fairness subsection in the evaluation chapter. Reporting a weakness you measured is a strength; having a reviewer discover it is not.

## 12. Transparency & explainability

Every finding must answer four questions without the user leaving the screen:
1. **What** was found (bilingual plain language)
2. **Where** (highlighted in the source)
3. **Why** it was flagged (the rule text, the matched source, the model's confidence)
4. **What to do** (a concrete, actionable fix)

Additionally: a public **methodology page** describing each detector, its measured accuracy, and its known limitations; engine version + rule-set version stamped on every report so results are reproducible and disputable.

## 13. Guardrails as executable tests

Encode the policy so it cannot silently rot:

```python
FORBIDDEN = ["plagiarism detected", "ai-written", "cheat", "fraud",
             "wykryto plagiat", "napisane przez AI", "oszustwo"]

def test_no_accusatory_language_in_ui_strings():
    for path in Path("frontend/src/i18n").rglob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        for term in FORBIDDEN:
            assert term not in text, f"Accusatory phrasing '{term}' in {path}"

def test_aigt_suppressed_for_short_segments():
    assert AigtDetector().analyse("word " * 100) == []

def test_aigt_excluded_from_readiness_score():
    assert "aigt" not in ReadinessScore.CONTRIBUTING_MODULES

def test_no_document_text_in_logs(caplog):
    run_analysis(fixture("thesis.docx"))
    assert SECRET_SENTINEL_SENTENCE not in caplog.text

def test_external_calls_blocked_without_consent(monkeypatch):
    with pytest.raises(ConsentRequired):
        run_analysis(fixture("thesis.docx"), consent=Consent.none())

def test_ssrf_private_ranges_blocked():
    for url in ["http://127.0.0.1/", "http://169.254.169.254/",
                "http://10.0.0.1/", "http://[::1]/"]:
        with pytest.raises(BlockedUrl):
            fetch_reference_url(url)
```

A CI suite that enforces your ethics policy is itself a contribution worth a paragraph in the thesis — and it is the mechanism that keeps the policy true in month 7, not just month 1.

## 14. Pre-launch checklist

**Security**
- [ ] All XML parsing uses `defusedxml`
- [ ] Zip bomb / ratio limits enforced and unit-tested
- [ ] No TeX engine is ever invoked
- [ ] Parsing runs in a resource-limited subprocess
- [ ] SSRF allowlist + private-range blocking on every outbound fetch
- [ ] `gitleaks` and `pip-audit` green in CI
- [ ] Rate limits active on upload and analyse
- [ ] Container runs non-root, read-only FS
- [ ] No document text in any log line
- [ ] Generic client errors; details server-side only

**Privacy**
- [ ] Retention job runs and is tested
- [ ] Delete-everything cascades verified
- [ ] Uploaded theses are never added to the corpus (enforced in code, not policy)
- [ ] External-call consent gate defaults to off
- [ ] Egress log visible to the user
- [ ] Processing notice shown at upload, in PL and EN

**Ethics**
- [ ] Accusatory-language test passes for both locales
- [ ] AIGT suppressed below the length threshold
- [ ] AIGT excluded from the aggregate score
- [ ] Limitations panel always shown with AIGT results
- [ ] Every similarity finding shows its matched source
- [ ] Methodology page published
- [ ] Engine + rule-set version on every report
