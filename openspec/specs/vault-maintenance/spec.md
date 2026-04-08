## Purpose

Vault file validation, auto-fixing, YAML normalization, and issue reporting. Ensures markdown files conform to arete's expected format and provides tools for bulk formatting.

## Requirements

### Requirement: Vault file validation
The system SHALL validate individual markdown files for arete compatibility, checking frontmatter structure, required fields, and card format.

#### Scenario: Valid file passes check
- **WHEN** the user runs `arete vault check somefile.md` on a well-formed file
- **THEN** the system reports no errors

#### Scenario: Invalid file reports errors
- **WHEN** a file has malformed frontmatter or missing required fields
- **THEN** the system reports specific errors with locations

### Requirement: Auto-fix common format errors
The system SHALL auto-fix common formatting issues including tab characters and missing cards list structure.

#### Scenario: Fix tabs in frontmatter
- **WHEN** the user runs `arete vault fix somefile.md` and the file contains tabs
- **THEN** tabs are replaced with spaces

### Requirement: YAML normalization
The system SHALL normalize YAML frontmatter to use stripped block scalar format for consistency.

#### Scenario: Format normalizes YAML
- **WHEN** the user runs `arete vault format`
- **THEN** all vault files' YAML frontmatter is normalized to block scalar style

#### Scenario: Dry-run format preview
- **WHEN** the user runs `arete vault format --dry-run`
- **THEN** the system shows what would change without modifying files

### Requirement: Issue reporting
The system SHALL track and display reported card issues, with the ability to clear individual or all reports.

#### Scenario: View reports
- **WHEN** the user runs `arete report`
- **THEN** all reported card issues are displayed

#### Scenario: Clear specific report
- **WHEN** the user runs `arete report --clear 3`
- **THEN** report at index 3 is removed
