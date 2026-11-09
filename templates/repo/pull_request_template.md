## What this changes

<!-- In plain words, from the user's side: what's different after this? -->

## Why

<!-- The problem it fixes or the feature it adds. Link the issue if there is one ("Fixes #12"). -->

## AI use

<!-- Required. Which tool or model, and roughly how much of this it wrote. Write "None" if none. -->

## Checks

<!-- Tick only what was actually done. See AGENTS.md → "Your pull request". -->

- [ ] **Validated**: `check.py` passed
- [ ] **Automated Test**: the pull request checks passed **and** the live check (`run.py --spec`) passed on a Cinnamon desktop
- [ ] **Manual Review**: leave unticked; the maintainer tries it on a real Cinnamon desktop before merging

If a check wasn't run, say why (the live check needs a Cinnamon desktop):

## How to try it

<!-- Numbered steps. Each one: something to do, and what should happen.
     Example: "Left-click the panel icon. A menu opens with Pause at the top."
     Write them from this change only. A one-line fix gets one or two steps. -->

1.

## Checklist

- [ ] One fix or feature only
- [ ] New tests added that fail without this change (if shipped code changed)
- [ ] No existing test, sample file or `harness.json` entry edited or removed (or explained above, for the maintainer to allow)
- [ ] No new programs, system services, web addresses or network use (or explained above, for the maintainer to allow)
- [ ] Nothing changed in `.github/`, `.claude/`, `AGENTS.md` or `CLAUDE.md` (or explained above, for the maintainer to allow)
- [ ] No edits to `files/<uuid>/modules/shared/` (those are copies; fix `cinnamon-tools/shared/` instead)
- [ ] `README.md` and `screenshot.png` updated (if behaviour, settings or looks changed)
- [ ] `version` and `CHANGELOG.md` left alone (the maintainer does those at release)
