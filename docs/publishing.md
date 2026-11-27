# Publishing Guide

This guide is for maintainers of the **Arete** project.

## Part 1: Python Package Distribution

End users install Arete directly from GitHub via `uv`:

```bash
uv tool install git+https://github.com/adamtrnguyen/Arete
```

The GitHub Release workflow also builds and attaches the wheel (`dist/*.whl`) and sdist (`dist/*.tar.gz`). Publishing to PyPI is optional and manual (`uv publish` with a PyPI API token); automated PyPI upload is not enabled.

---

## Part 2: GitHub Actions (CI/CD)

The project uses GitHub Actions to automate testing and releases.

-   **CI Workflow** (`ci.yml`): Runs on every PR and push to `main`. Unit tests with coverage on Linux, macOS and Windows; lint, format, types, import contracts, dead code, generated docs, and version agreement; plugin lint, format, import rules, `minAppVersion`, strict type check, tests with coverage, and build (Linux + Windows); integration tests against Dockerized Anki.
-   **Release Workflow** (`release.yml`): Triggered when a SemVer tag is pushed without a leading `v` (e.g., `4.1.0`, matching Obsidian Community Plugins and BRAT) or with a `v` prefix (e.g., `v4.1.0`).
    -   Verifies the tag (with any optional leading `v` stripped) matches `pyproject.toml`.
    -   Runs `uv run just release` (the full `just qa` gate, Python `dist/` sdist/wheel, Obsidian plugin `main.js`/`manifest.json`/`styles.css`, Anki add-on `arete_ankiconnect.zip` + `arete_ankiconnect.ankiaddon`, and wheel CLI smoke test via `verify-wheel`).
    -   Attaches all release artifacts to the GitHub Release.

---

## Part 3: Obsidian Community Plugins

To get your plugin into the official list inside Obsidian, you must submit a Pull Request to the Obsidian team.

### Prerequisites
1.  **GitHub Release**: You must have a published Release with `main.js`, `manifest.json`, and `styles.css` attached, tagged with the exact `manifest.json` version (no leading `v`, e.g., `4.1.0`). `release.yml` supports bare SemVer tags natively so the same release serves both Obsidian Community Plugins and BRAT.
2.  **Repo Structure**: `manifest.json` and `versions.json` must be at the root of the repo (both are mirrored from `obsidian-plugin/` and verified by `scripts/sync_versions.py`).

### Submission Steps
1.  **Fork** the [obsidian-releases](https://github.com/obsidianmd/obsidian-releases) repository.
2.  **Add Entry**: Add your plugin to `community-plugins.json` in their repo.
    *   Format:
        ```json
        {
          "id": "arete",
          "name": "Arete",
          "author": "Adam Nguyen",
          "description": "One-way sync from your vault to Anki. Supports media, MathJax, and automatic card management.",
          "repo": "adamtrnguyen/Arete"
        }
        ```
3.  **Submit PR**: Open a Pull Request on their repo.

---

## Part 4: Versioning Strategy & Release Checklist

We follow **Semantic Versioning (SemVer)**: `MAJOR.MINOR.PATCH`.

-   **MAJOR**: Breaking changes (e.g., changes to the Markdown card syntax).
-   **MINOR**: New features (e.g., support for a new Anki field type).
-   **PATCH**: Bug fixes and documentation updates.

Before tagging a release (e.g., `4.1.0`):

1.  Add the release section to `CHANGELOG.md`.
2.  Bump `version` in `pyproject.toml` only.
3.  Run `just sync-versions` to write the version into `manifest.json`, `versions.json`, `obsidian-plugin/manifest.json`, `obsidian-plugin/package.json`, `obsidian-plugin/versions.json`, `arete_ankiconnect/manifest.json`, and refresh `uv.lock`.
4.  Sync `obsidian-plugin/package-lock.json`:
    ```bash
    npm --prefix obsidian-plugin install --package-lock-only --legacy-peer-deps
    ```
5.  Run `just qa` and `just test-integration` (`just qa` and `just release` fail if any manifest or `versions.json` disagrees).
6.  Commit, push `main`, tag `<version>` (e.g., `4.1.0`, no `v` prefix), and push the tag.

---

## Part 5: AnkiWeb (Add-on Store)

Publishing to AnkiWeb is currently a **manual process**.

1.  **Download the Artifact**: Go to the GitHub Release you just created (e.g., `4.1.0`) and download `arete_ankiconnect.ankiaddon` directly.
2.  **Upload**:
    *   Go to [AnkiWeb Shared Add-ons](https://ankiweb.net/shared/addons/).
    *   Click **Upload**.
    *   Select `arete_ankiconnect.ankiaddon`.
    *   Update the description if necessary.

*(Note: Automated publishing is disabled due to the lack of a reliable public uploading tool.)*

---

## Part 6: BRAT (Beta Testing Distribution)

The **BRAT** (Beta Reviewers Auto-update Tool) plugin allows users to install Obsidian plugins directly from GitHub before they are officially published.

### How it works
BRAT looks for the released artifacts (`main.js`, `manifest.json`, `styles.css`) on the **latest GitHub Release** (`/releases/latest`). Since `release.yml` attaches these exact files to every release, **Arete is BRAT-compatible out of the box.**

### Instructions for Users
To test the pre-release version of Arete:
1.  Install the **BRAT** plugin from the Obsidian Community Store.
2.  Open BRAT settings -> "Add Beta plugin".
3.  Enter the repository URL: `https://github.com/adamtrnguyen/Arete`.
4.  BRAT will download the latest release and keep it auto-updated.

### Important for Maintainers
-   Tag releases with the exact `manifest.json` version (e.g., `4.1.0`, without a leading `v`) so the release works natively for both Obsidian Community Plugins and BRAT.
-   Do **not** change the filenames of the attached binaries (`main.js`, `manifest.json`, `styles.css`). Both Obsidian and BRAT expect these exact names.
