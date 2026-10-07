# Contribuer

## Environnement de développement

### Option A — Devcontainer (recommandé)

1. Ouvre le dépôt dans VS Code avec l'extension "Dev Containers" installée.
2. "Reopen in Container" — l'image installe Python, `homeassistant` et les dépendances de test.
3. Lance `scripts/develop` pour démarrer une vraie instance Home Assistant locale (port 8123) avec cette intégration chargée dans `config/custom_components/a_petits_pas`. Ajoute-la depuis l'UI (Paramètres → Appareils et services) pour tester le config flow de bout en bout.

### Option B — venv local

```bash
scripts/setup
```

Crée un environnement virtuel `.venv` avec les dépendances de test installées.

## Lancer les tests

```bash
scripts/lint
```

Lance `ruff` (lint + format check), `mypy` et `pytest` — les mêmes vérifications que la CI.

Pour lancer seulement les tests :

```bash
pytest tests/
```

## Fixtures de test

Les fixtures dans `tests/fixtures/` sont construites à la main avec des données fictives (noms, identifiants). Ne jamais committer de données réelles/personnelles issues d'une vraie capture réseau.

## Traductions

`strings.json` est la source canonique. `translations/fr.json` doit rester identique (c'est le public cible principal), `translations/en.json` est la traduction anglaise. Toute nouvelle entité/erreur/étape de config flow doit être ajoutée aux trois fichiers.
