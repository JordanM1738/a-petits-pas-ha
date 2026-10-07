# ha-a-petits-pas

Intégration [Home Assistant](https://www.home-assistant.io/) (via [HACS](https://hacs.xyz/)) pour le journal de bord de [Journal à petits pas](https://app.journalapetitspas.ca), une application utilisée par plusieurs CPE/garderies au Québec (plateforme Amisgest).

## ⚠️ Avertissement — projet non officiel

Cette intégration **n'est pas officielle** et n'est ni affiliée à, ni approuvée par Amisgest ou Journal à petits pas. Elle a été construite par rétro-ingénierie du trafic réseau de l'application web (aucune documentation publique de l'API n'existe).

- Elle peut **cesser de fonctionner sans préavis** si Amisgest modifie son service.
- Son usage pourrait ne pas être conforme aux conditions d'utilisation du service — **à tes risques**.
- L'intégration ne fait aucune tentative de dissimuler son identité (`device_info` s'identifie comme une intégration Home Assistant) ni de contourner une limite de débit éventuelle : l'intervalle de sondage par défaut est volontairement bas (5 minutes).

## Fonctionnalités

Pour chaque enfant lié au compte parent configuré, l'intégration crée un appareil Home Assistant avec :

| Entité | Description |
|---|---|
| `sensor.<enfant>_dernier_journal_de_bord` | Générique, fonctionne toujours : état = date de la dernière entrée, attributs = liste complète des activités (type, commentaire, heures, etc.) |
| `sensor.<enfant>_dernier_repas`, `_derniere_sieste`, `_derniere_couche`, `_dernier_biberon`, `_dernieres_informations`, `_redige_par` | Capteurs structurés, toujours créés pour chaque enfant (même si la garderie n'utilise jamais la catégorie : ils restent alors `unknown`, avec des attributs présents mais vides). Pour les activités avec une plage horaire (ex. sieste), l'état est formaté `HH:MM - HH:MM`. Passent à `unknown` tant qu'aucune entrée n'a été rédigée pour la journée (ex. juste après minuit) ; `unavailable` est réservé aux vrais échecs de récupération des données (panne réseau, API en erreur) |
| `sensor.<enfant>_presence_aujourdhui` | Statut de présence/absence confirmée pour la journée courante (via `confirmationAbs`) — best-effort, voir Limitations connues |
| `image.<enfant>_photo_de_profil` | Photo de profil de l'enfant, récupérée côté serveur (authentifiée) et servie localement par Home Assistant |
| `binary_sensor.<enfant>_nouveau_journal_de_bord` | S'active pendant un cycle de rafraîchissement lorsqu'une nouvelle entrée est détectée (comportement "pulse") |
| Événement `a_petits_pas_new_post` | Émis en même temps que le capteur binaire, avec les détails en données d'événement — à utiliser dans tes automatisations |

Les capteurs structurés par type d'activité sont fiables (`activity_type_id` est une vraie clé de dictionnaire), mais peuvent varier d'une garderie à l'autre.

**Hors scope pour l'instant** : le "mur" de publications générales (`wall_post`, messages/photos/publications narratives type "Journal - 18 mois+") et les fiches d'assiduité à signer. Voir la feuille de route ci-dessous.

## Installation via HACS

1. Dans Home Assistant : **HACS → Intégrations → ⋮ (menu en haut à droite) → Dépôts personnalisés**.
2. Ajoute l'URL de ce dépôt (`https://github.com/JordanM1738/a-petits-pas-ha`), catégorie **Intégration**.
3. Installe "Journal à petits pas" depuis HACS, puis redémarre Home Assistant.
4. **Paramètres → Appareils et services → Ajouter une intégration → Journal à petits pas**, entre ton courriel et ton mot de passe.

## Configuration

- Aucun paramètre requis à l'installation autre que les identifiants.
- Options (⚙️ sur l'intégration) : intervalle de rafraîchissement en minutes (5 par défaut).

## Exemple d'automatisation

```yaml
automation:
  - alias: "Nouveau journal de bord - notification mobile"
    trigger:
      - platform: event
        event_type: a_petits_pas_new_post
    action:
      - service: notify.mobile_app_mon_telephone
        data:
          title: "Nouveau journal de bord pour {{ trigger.event.data.child_name }}"
          message: "Une nouvelle entrée est disponible dans le journal de bord."
```

## Limitations connues

- Le format des réponses de certains endpoints (`api/wall/wallposts`) varie (JSON brut ou base64) — géré automatiquement, mais documenté ici comme signe de la fragilité générale d'une API non documentée.
- Le mappage `activity_type_id → catégorie` (repas/sieste/couche/rédigé par) a été établi à partir d'un seul compte/garderie ; il pourrait ne pas correspondre exactement partout.
- Le capteur de présence (`code_ph` renvoyé par `confirmationAbs`) traduit les codes confirmés `P`→"Présent", `A`→"Absent", et `EXPECTED`→"Attendu" (ce dernier pas encore confirmé en direct). Tout autre code est affiché tel quel (non traduit) plutôt que caché — dis-moi ce que tu observes pour qu'on l'ajoute à `PRESENCE_CODE_LABELS` dans `const.py`.
- Les libellés du capteur de présence sont codés en dur en français (comme le reste du contenu qui vient des éducatrices) : ils ne suivent pas la langue configurée dans Home Assistant.
- Voir le fichier de plan du projet pour la liste complète des incertitudes techniques encore à vérifier.

## Feuille de route

- **v0.1.0 (actuelle)** — journal de bord (repas/sieste/couche/rédigé par), présence du jour, photo de profil, notification de nouvelle entrée.
- **v0.2.0** — le "mur" de publications générales (messages, photos, publications narratives).
- **v0.3.0** — fiches d'assiduité à signer.

## Contribuer

Voir [CONTRIBUTING.md](CONTRIBUTING.md) pour l'environnement de développement (devcontainer) et comment lancer les tests.
