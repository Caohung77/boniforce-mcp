# Privacy Policy Addendum — MCP Connector

**Target:** add this section to
[https://www.boniforce.de/datenschutzerklaerung-2/](https://www.boniforce.de/datenschutzerklaerung-2/),
either as a new top-level section "MCP-Connector (KI-Assistenten)"
between **SaaS Platform Processing** and **Policy Changes**, or as a
standalone subpage at `/datenschutzerklaerung-mcp/`. Required to pass
Anthropic Connectors Directory review.

## Gap analysis of the existing policy

Audit of `boniforce.de/datenschutzerklaerung-2/` shows the policy
already covers:

- ✅ OpenAI Ireland Ltd. as subprocessor (general AI processing)
- ✅ Data retention periods (30 d logs, 360 d snapshots, 6–10 y contracts)
- ✅ International transfers, SCCs
- ✅ Data subject rights, contact
- ✅ Security measures (general)

The policy **does not cover** the MCP-connector specifics that
Anthropic's submission reviewers look for. Missing topics:

| Missing topic | Why Anthropic asks |
|---|---|
| API-key storage & encryption (Fernet) | Token-handling transparency |
| `sha256(token)` user-identifier model | Pseudonymisation evidence |
| Data flow to Anthropic / OpenAI via MCP | Subprocessor disclosure |
| Sectorbench as upstream data source | Subprocessor list completeness |
| MCP-specific retention (OAuth codes 10 min, refresh tokens until revocation) | Retention granularity |
| Chat-content non-forwarding | Anti-leak guarantee |
| Revocation path | User-control requirement |

Without these the policy is "incomplete" by Anthropic's standard, which
their docs flag as cause for **immediate rejection**.

---

## Suggested German section text

> ## MCP-Connector (Boniforce für ChatGPT, Claude und andere KI-Assistenten)
>
> Boniforce betreibt unter `mcp.boniforce.de` einen Model-Context-Protocol
> (MCP)-Server, der die Boniforce-API als Tools für KI-Assistenten wie
> Anthropic Claude und OpenAI ChatGPT zugänglich macht. Dieser Abschnitt
> beschreibt die zusätzlichen Verarbeitungen, die durch die Nutzung des
> MCP-Connectors entstehen.
>
> ### Welche Daten werden verarbeitet
>
> - **Boniforce-API-Schlüssel des Nutzers** (`sk_live-…`):
>   bei der erstmaligen Verbindung übermittelt der Nutzer seinen
>   persönlichen Boniforce-API-Schlüssel über ein über HTTPS gesichertes
>   Formular auf `mcp.boniforce.de/oauth/login`.
> - **Anfrage-Parameter**: Firmenname, Registerart, Registernummer,
>   Registergericht, Branchenschlüssel, Zeiträume.
> - **Inhaltsdaten** (z. B. Chat-Verlauf, freie Texte) werden **nicht**
>   an Boniforce übertragen. Das KI-Modell sendet uns ausschließlich die
>   strukturierten Argumente, die zur Beantwortung der Nutzeranfrage
>   technisch erforderlich sind.
>
> ### Wie wir den API-Schlüssel schützen
>
> - **Verschlüsselung at rest:** der API-Schlüssel wird vor Speicherung
>   mittels Fernet (AES-128-CBC mit HMAC-SHA-256) verschlüsselt. Der
>   Schlüsselmaterial liegt ausschließlich in einer serverseitigen
>   Umgebungsvariable und wird zu keinem Zeitpunkt protokolliert oder
>   gespiegelt.
> - **Pseudonymisierung der Nutzer-ID:** der MCP-Server identifiziert
>   Nutzer ausschließlich über `sha256(API-Schlüssel)`. Dieser Hash ist
>   nicht reversibel; aus dem gespeicherten Hash kann der ursprüngliche
>   Schlüssel nicht rekonstruiert werden.
> - **Validierung:** bei jeder Eingabe wird der Schlüssel einmal gegen
>   `api.boniforce.de` validiert. Schlägt die Validierung fehl, wird der
>   Schlüssel verworfen und kein Datensatz erzeugt.
>
> ### OAuth-Tokens und Sitzungen
>
> - **Access-Token (JWT, RS256):** 1 Stunde gültig, audience-gebunden an
>   `mcp.boniforce.de/mcp`. Nicht serverseitig gespeichert.
> - **Refresh-Tokens:** zufällig erzeugte opake Strings, single-use,
>   ausschließlich gehasht (SHA-256) gespeichert.
> - **Authorization-Codes (PKCE S256):** flüchtig, gültig für 10 Minuten,
>   nach erster Verwendung sofort invalidiert. Plain-PKCE wird gemäß
>   OAuth 2.1 ausnahmslos abgelehnt.
> - **Origin-Validierung:** `/mcp` akzeptiert ausschließlich Anfragen von
>   den freigegebenen Ursprüngen `claude.ai`, `chatgpt.com` oder dem
>   eigenen Issuer-Ursprung. Alle anderen Origins werden mit HTTP 403
>   abgelehnt.
>
> ### Empfänger und Auftragsverarbeiter (MCP-bezogen)
>
> Zusätzlich zu den im Hauptabschnitt "Auftragsverarbeiter und
> Dienstleister" genannten Verarbeitern:
>
> - **Anthropic, PBC**, San Francisco, USA — Betreiberin von Claude.ai
>   und Claude Desktop. Empfängt nur die vom Nutzer in den
>   KI-Assistenten eingegebenen Inhalte; erhält **keinen** direkten
>   Zugriff auf den Boniforce-API-Schlüssel.
> - **OpenAI OpCo, LLC**, San Francisco, USA — Betreiberin von ChatGPT.
>   Selbe Datenflüsse wie Anthropic.
> - **Sectorbench (The AI Whisperer GmbH)** — Branchen-Datenquelle. Wird
>   ausschließlich mit einem operator-seitigen Sammeltoken aufgerufen;
>   für Sectorbench bleiben die Endnutzer anonym.
> - **STRATO GmbH** (bzw. der vom Betreiber genutzte Cloud-Hoster) —
>   physischer Betrieb des MCP-Servers; siehe Hauptabschnitt.
>
> Übermittlungen in die USA erfolgen auf Basis der EU-Standardvertrags­
> klauseln; Anthropic und OpenAI haben die jeweiligen
> Datenverarbeitungs­verträge unterzeichnet.
>
> ### Speicherdauer
>
> | Datenkategorie | Aufbewahrung |
> |---|---|
> | Verschlüsselter API-Schlüssel | bis zur Rotation oder Widerruf durch den Nutzer |
> | Refresh-Token (Hash) | bis zum Widerruf oder bis 90 Tage Inaktivität |
> | Access-Token (JWT) | nicht gespeichert; nur Laufzeit-Validierung |
> | OAuth-Authorization-Codes | 10 Minuten |
> | Anfrage-Logs (anonym) | maximal 30 Tage |
> | Ergebnis-Caches (Branchen-Daten) | maximal 10 Minuten in-memory |
>
> ### Rechtsgrundlage
>
> Die Verarbeitung erfolgt zur Erfüllung des Nutzungsvertrags mit dem
> Boniforce-Kunden (Art. 6 Abs. 1 lit. b DSGVO). Eine Verarbeitung zu
> Trainingszwecken durch Boniforce, Anthropic oder OpenAI findet **nicht
> statt**; die zero-retention-Konfiguration im AI-Subprocessor schließt
> Trainingsnutzung aus.
>
> ### Widerruf und Sperrung
>
> Der Nutzer kann seinen Boniforce-API-Schlüssel jederzeit im
> Boniforce-Dashboard widerrufen oder neu erzeugen. Mit dem Widerruf
> verliert der Connector unverzüglich den Zugriff. Auf Anforderung
> löscht Boniforce zusätzlich die mit der pseudonymisierten Nutzer-ID
> verknüpften OAuth-Datensätze.
>
> ### Kontakt
>
> Datenschutzanfragen zum MCP-Connector richten Sie bitte an
> *(Datenschutzbeauftragter-E-Mail-Adresse einfügen)*. Der allgemeine
> Datenschutzkontakt ist im Hauptabschnitt "Kontakt" angegeben.

---

## Suggested English section text

(Add only if Boniforce publishes a parallel English privacy policy.
Otherwise the German section above is sufficient — Anthropic accepts
non-English policies.)

> ## MCP Connector (Boniforce for ChatGPT, Claude, and other AI assistants)
>
> Boniforce operates an MCP (Model Context Protocol) server at
> `mcp.boniforce.de` that exposes the Boniforce API as tools usable by
> AI assistants such as Anthropic Claude and OpenAI ChatGPT. This
> section describes the additional processing introduced by using the
> MCP connector.
>
> **Data processed:** user's Boniforce API key (entered once over HTTPS),
> structured tool arguments (company name, register data, sector key,
> time windows). **No chat content** is transmitted to Boniforce — the
> AI model only sends the structured arguments needed to answer the
> user's question.
>
> **API-key protection:** Fernet-encrypted at rest (AES-128-CBC + HMAC-
> SHA-256), key material lives only in a server-side environment
> variable, never logged. User identifier is `sha256(API-key)` — one-way
> hash; the stored hash cannot be reversed to the original key. The key
> is validated against `api.boniforce.de` once on entry and discarded if
> invalid.
>
> **OAuth tokens:** RS256 JWT access tokens, 1-hour TTL, audience-bound
> to `mcp.boniforce.de/mcp`, not stored server-side. Refresh tokens are
> opaque, single-use, SHA-256-hashed before storage. PKCE codes (S256
> only — plain rejected per OAuth 2.1) are valid for 10 minutes and
> invalidated on first use. The `/mcp` endpoint accepts only requests
> with an allowlisted Origin (`claude.ai`, `chatgpt.com`, or the issuer
> origin) — others are rejected with HTTP 403.
>
> **Additional MCP-specific subprocessors:** Anthropic (Claude), OpenAI
> (ChatGPT), Sectorbench (sector data, called with operator token —
> end-users anonymous). US transfers under EU Standard Contractual
> Clauses; zero-retention DPAs in place.
>
> **Retention:** encrypted API key — until rotation or revocation;
> refresh-token hash — until revocation or 90 d inactivity; OAuth codes
> — 10 minutes; request logs — max 30 days; sector-data cache — max 10
> minutes in-memory.
>
> **Legal basis:** Art. 6(1)(b) GDPR — contract performance. No training
> use by Boniforce, Anthropic, or OpenAI.
>
> **Revocation:** rotate or revoke the Boniforce API key in your
> dashboard — connector access stops on the next call. Deletion of
> pseudonymised OAuth records on request.

---

## DPO review checklist

Before going live, the Boniforce DPO should confirm:

1. ☐ Anthropic and OpenAI are listed in the public subprocessor table.
2. ☐ A signed Data Processing Agreement is on file with each.
3. ☐ Standard Contractual Clauses (Module 1 or 2 as appropriate) are
   attached.
4. ☐ Zero-retention is contractually confirmed for AI subprocessors.
5. ☐ The DPO email above is reachable.
6. ☐ The page is reachable at a stable URL with HTTP 200 over HTTPS.
7. ☐ A timestamp / "Stand:" line is added at the bottom.

When all seven boxes are checked, the URL is ready for the Anthropic
submission form.
