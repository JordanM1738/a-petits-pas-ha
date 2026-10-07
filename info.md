# Journal à petits pas

Intégration Home Assistant (non officielle) pour le journal de bord de [Journal à petits pas](https://app.journalapetitspas.ca) (plateforme Amisgest), utilisé par de nombreux CPE/garderies au Québec.

Pour chaque enfant lié à ton compte, tu obtiens :
- un capteur "Dernier journal de bord" (résumé + toutes les activités en attributs),
- des capteurs par type d'activité observé (repas, sieste, couche, rédigé par),
- un capteur binaire "Nouveau journal de bord" + un événement Home Assistant `a_petits_pas_new_post" pour déclencher tes propres automatisations/notifications.

## ⚠️ Avertissement

Cette intégration utilise une API privée non documentée, obtenue par rétro-ingénierie du trafic de l'application web. Elle n'est ni affiliée à, ni approuvée par Amisgest, et peut cesser de fonctionner sans préavis si le service change. Utilisation à tes propres risques — voir le README pour les détails.
