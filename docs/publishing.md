# Publishing Guide

This guide is for maintainers of the **Arete** project.

## Part 1: PyPI (The Python Package)

### 1. Check if it exists
Go to: [https://pypi.org/project/arete/](https://pypi.org/project/arete/)
*If you see a 404, it is NOT published yet.*

### 2. How to Publish
You need to upload the artifacts built in `dist/`.

1.  **Create Account**: [Register on PyPI](https://pypi.org/account/register/).
2.  **Get Token**: [Account Settings](https://pypi.org/manage/account/) -> API Tokens -> Add API Token (Scope: Entire Account).
3.  **Run Command**:
    ```bash
    uv publish
    ```
    *   **Username**: `__token__`
    *   **Password**: `pypi-AgEIpy...` (Your full token)

Once successful, `pip install arete` will work for everyone.

---

## Part 2: GitHub Actions (CI/CD)

The project uses GitHub Actions to automate testing and releases.

-   **Test Workflow**: Runs on every PR and push to `main`. It executes `pytest` (with Dockerized Anki) and `ruff`.
-   **Release Workflow**: Triggered when a new GitHub Tag is pushed (e.g., `v1.0.0`).
    -   Automatically builds the Python `dist/` artifacts.
    -   Automatically builds the Obsidian Plugin (`main.js`, `manifest.json`, `styles.css`).
    -   Attaches all binaries to the GitHub Release.
    -   (Optional) Pushes to PyPI if Trusted Publishing is configured.

---

## Part 3: Obsidian Community Plugins

To get your plugin into the official list inside Obsidian, you must submit a Pull Request to the Obsidian team.

### Prerequisites
1.  **GitHub Release**: You must have a published Release with `main.js`, `manifest.json`, and `styles.css` attached.
2.  **Repo Structure**: `manifest.json` must be at the root of the repo (or root of released files). *Note: We release from `obsidian-plugin/`, but the build process handles the file placement.*

### Submission Steps
1.  **Fork** the [obsidian-releases](https://github.com/obsidianmd/obsidian-releases) repository.
2.  **Add Entry**: Add your plugin to `community-plugins.json` in their repo.
    *   Format:
        ```json
        {
          "id": "arete",
          "name": "Arete",
          "author": "Adam Nguyen",
          "description": "Sync Obsidian vault to Anki with topological queues and FSRS support.",
          "repo": "adamtrnguyen/Arete"
        }
        ```
3.  **Submit PR**: Open a Pull Request on their repo.

---

## Part 4: Versioning Strategy

We follow **Semantic Versioning (SemVer)**: `MAJOR.MINOR.PATCH`.

-   **MAJOR**: Breaking changes (e.g., changes to the Markdown card syntax).
-   **MINOR**: New features (e.g., support for a new Anki field type).
-   **PATCH**: Bug fixes and documentation updates.

Always update `version` in `pyproject.toml` and `manifest.json` simultaneously to keep the CLI and Plugin in sync.

---

## Part 5: AnkiWeb (Add-on Store)

## Part 5: AnkiWeb (Add-on Store)

Publishing to AnkiWeb is currently a **manual process**.

1.  **Download the Artifact**: Go to the GitHub Release you just created (e.g., `v2.0.0`) and download `arete_ankiconnect.zip`.
2.  **Rename**: Rename the file to `arete_ankiconnect.ankiaddon` if it isn't already (GitHub Actions artifact downloads sometimes strip extensions).
3.  **Upload**:
    *   Go to [AnkiWeb Shared Add-ons](https://ankiweb.net/shared/addons/).
    *   Click **Upload**.
    *   Select the file.
    *   Update the description if necessary.

*(Note: Automated publishing is disabled due to the lack of a reliable public uploading tool.)*

---

## Part 6: BRAT (Beta Testing Distribution)

The **BRAT** (Beta Reviewers Auto-update Tool) plugin allows users to install Obsidian plugins directly from GitHub before they are officially published.

### How it works
BRAT looks for the released artifacts (`main.js`, `manifest.json`, `styles.css`) in your **GitHub Releases**. Since our CI/CD pipeline already attaches these files to every release, **Arete is native-BRAT compatible out of the box.**

### Instructions for Users
To test the pre-release version of Arete:
1.  Install the **BRAT** plugin from the Obsidian Community Store.
2.  Open BRAT settings -> "Add Beta plugin".
3.  Enter the repository URL: `https://github.com/adamtrnguyen/Arete`.
4.  BRAT will download the latest release and keep it auto-updated.

### Important for Maintainers
-   Ensure the `manifest.json` version matches the GitHub Release tag.
-   Do **not** change the filenames of the attached binaries (`main.js`, etc.). BRAT expects these exact names.
