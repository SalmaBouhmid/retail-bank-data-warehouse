# Cahier des charges — Retail Bank Data Warehouse

> Projet portfolio n° 2 de Salma Bouhmid (DUT data engineering, EST). Objectif : décrocher un stage de fin d'études
> (avril 2027) dans une **banque, une assurance ou une ESN** au Maroc, et préparer une candidature en licence/master à l'étranger.
> Ce document est la référence unique du projet : toute nouvelle session Claude Code doit le lire en premier.

## 1. Pitch (ce que le recruteur doit comprendre en 10 secondes)

Un **entrepôt de données bancaire** construit sur les **vraies données anonymisées d'une banque de détail**
(jeu de données « Berka », PKDD'99 : 5 300 clients, ≈ 1 million de transactions, prêts, cartes) :
ingestion automatisée → PostgreSQL → modèles **dbt** testés → **reporting Power BI** pour la direction
(activité, clientèle, risque de crédit), le tout lancé par **Airflow** dans **Docker**.

Phrase CV : *« Entrepôt de données bancaire (1 M de transactions réelles anonymisées) : pipeline ELT orchestré par Airflow,
modèles dbt testés (schéma en étoile), PostgreSQL sous Docker, reporting Power BI sur l'activité et le risque de crédit. »*

## 2. Questions métier (ce que le reporting doit permettre)

| # | Question d'un directeur d'agence / du risque | Indicateurs |
|---|---|---|
| Q1 | Qui sont nos clients et où sont-ils ? | clients par région, âge, sexe, type de compte, ancienneté |
| Q2 | Comment évolue l'activité ? | nombre et montant des transactions par mois, type (crédit/débit), canal, solde moyen |
| Q3 | Quels prêts posent problème ? | taux de défaut par montant, durée, région, profil ; montant à risque |
| Q4 | Peut-on repérer un client à risque **avant** le prêt ? | indicateurs calculés **uniquement avec les transactions antérieures à la date du prêt** (contrainte temporelle, sinon fuite de données) |
| Q5 | Qui détient quelles cartes ? | cartes par type (classic, gold, junior), par segment |
| Q6 | Les données sont-elles fiables ? | rapport de qualité : clés orphelines, doublons, valeurs manquantes, soldes incohérents |

## 3. Données

- **Source** : base publique du Czech Technical University — https://relational.fel.cvut.cz/dataset/Financial
  (serveur MariaDB `relational.fel.cvut.cz:3306`, compte invité public indiqué sur la page ; base `financial`).
  Port 3306 vérifié ouvert le 2026-11-06.
- **8 tables** : `account`, `client`, `disp` (lien client ↔ compte), `card`, `loan`, `order` (ordres permanents),
  `trans` (transactions), `district` (données démographiques par district). ≈ 79 Mo.
- Période 1993–1998, banque tchèque. **Le dire honnêtement** dans le README (données réelles anonymisées, publiques, utilisées
  en recherche) ; les montants sont en couronnes tchèques (CZK) — garder la devise, ne pas inventer une conversion en MAD.
- Les codes sont en tchèque (ex. `PRIJEM` = crédit, `VYDAJ` = débit, `VYBER` = retrait) : créer une **table de correspondance**
  documentée (seed dbt) au lieu de traduire en dur.
- Prêts : statut A (terminé OK), B (terminé non remboursé), C (en cours OK), D (en cours en retard) → défaut = B ou D.

## 4. Architecture

```
MariaDB publique (Berka) ──► extract.py (Python) ──► data/raw/*.parquet   (idempotent, horodaté)
                                                        │
                                                        ▼
                                           PostgreSQL : schéma raw
                                                        │  dbt
                                                        ▼
                     staging (nettoyage, renommage anglais, types, codes traduits)
                                                        ▼
                     intermediate (soldes mensuels, indicateurs avant prêt…)
                                                        ▼
                     marts : dim_client, dim_account, dim_district, dim_date,
                             fct_transactions, fct_loans, fct_monthly_balance
                                                        │
                       ┌────────────────────────────────┼─────────────────────┐
                       ▼                                ▼                     ▼
                Power BI (.pbix + PDF)        rapport qualité (HTML)    dbt docs (lineage)

Orchestration : Airflow (1 DAG : extract → load → dbt build → tests → rapport qualité)
Tout tourne avec : docker compose up
```

## 5. Stack (et pourquoi — à savoir dire en entretien)

| Outil | Rôle dans le projet | Phrase à savoir dire |
|---|---|---|
| Python (pandas, SQLAlchemy, pymysql) | extraction et chargement | « J'extrais en Parquet pour garder une copie brute rejouable. » |
| PostgreSQL | entrepôt | « La base relationnelle la plus utilisée en entreprise, open source. » |
| dbt | transformations SQL versionnées et **testées** | « Chaque table est un SELECT ; dbt gère l'ordre, les tests et la doc. » |
| Airflow | orchestration, relance, historique des exécutions | « Un DAG = les étapes et leurs dépendances. » |
| Docker Compose | lancer Postgres + Airflow d'une commande | « N'importe qui relance le projet sans rien installer à la main. » |
| Power BI | reporting métier | l'outil le plus demandé dans les offres de stage au Maroc |
| pytest, ruff, GitHub Actions | qualité, intégration continue | « Chaque push lance les tests et `dbt build` sur une base Postgres temporaire. » |

## 6. Phases (chaque phase se termine par un point d'apprentissage)

| Phase | Contenu | Salma doit savoir expliquer |
|---|---|---|
| 0 | Installer Docker Desktop et Power BI Desktop (Salma les installe elle-même), créer le dépôt | ce qu'est un conteneur |
| 1 | `extract.py` : 8 tables → Parquet ; tests (nombre de lignes, colonnes) | extraction idempotente, pourquoi Parquet |
| 2 | `docker-compose.yml` avec Postgres ; `load.py` → schéma `raw` | ce que fait `docker compose up` |
| 3 | dbt staging + seeds (codes tchèques) + tests `unique`, `not_null`, `relationships`, `accepted_values` | ce qu'est un modèle dbt et un test dbt |
| 4 | dbt marts en étoile + `dim_date` + indicateurs avant prêt (contrainte temporelle) | table de faits vs dimension ; fuite de données |
| 5 | Airflow : un DAG quotidien (même si la source ne change pas : montrer relance, logs, échecs) | DAG, tâche, dépendance, retry |
| 6 | Rapport qualité automatique (HTML) | les 4 contrôles et pourquoi |
| 7 | Power BI : 3 pages (Vue d'ensemble, Clients & activité, Risque crédit) ; export PDF + captures | une mesure DAX simple, un filtre |
| 8 | README anglais + `README.fr.md`, schéma d'architecture, captures, CI verte, tag v1.0 | le pitch de 30 s |
| 9 | Fiche entretien dans `pour_moi/` (non publiée) : 12 questions-réponses + lexique FR/EN | tout le projet |

Option bonus (seulement si le temps le permet) : petit modèle de risque (régression logistique) sur les indicateurs avant prêt,
en respectant la contrainte temporelle ; peu de prêts (682) → le présenter comme une analyse, pas comme un modèle de production.

## 7. Exigences de qualité

- Code Python et noms dbt **en anglais** (marché international), commentaires courts ; textes du README FR + EN.
- Aucun secret dans Git (`.env` dans `.gitignore`, `.env.example` versionné).
- Données brutes et base **non versionnées** si > 50 Mo ; tout est recréable par le pipeline.
- Chaque chiffre du README vient d'une requête ou d'un fichier généré, **jamais inventé**.
- Commits **au nom de Salma uniquement** (pas de ligne « Co-Authored-By »).
- Tests : au moins 20 tests pytest + tous les tests dbt verts ; CI GitHub Actions (ruff, pytest, `dbt build` avec un service Postgres).

## 8. Définition de « terminé »

- [ ] `docker compose up` puis déclenchement du DAG → entrepôt complet sans intervention.
- [ ] `dbt build` vert, `dbt docs` généré (capture du lineage dans le README).
- [ ] Rapport Power BI (.pbix + PDF + 3 captures) répondant à Q1–Q6.
- [ ] Dépôt public `SalmaBouhmid/retail-bank-data-warehouse`, CI verte, épinglé sur le profil.
- [ ] Fiche entretien dans `pour_moi/`, et Salma sait répondre aux questions des phases 1 à 7.

## 9. Contraintes de l'environnement

- Windows 11, Python Anaconda : `C:\Users\123\anaconda3\python.exe`. Git configuré (« Salma bouhmid »), push GitHub via le gestionnaire
  d'identifiants déjà enregistré. `gh` CLI non installé. Docker **pas encore installé** (phase 0).
- Salma est en cours d'études (peu de temps) : avancer par petites phases livrables, expliquer simplement en français,
  et terminer chaque phase par 3 questions de vérification pour elle.
