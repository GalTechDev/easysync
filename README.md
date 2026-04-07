# EasySync

Synchronisation universelle d'états Python en temps réel sur le réseau.

Manipulez vos objets Python comme si le réseau n'existait pas. EasySync intercepte les mutations d'attributs via un proxy transparent et les propage instantanément à toutes les machines connectées au serveur.

## Installation

```bash
pip install easysync
```

## Démarrage rapide

**Serveur** (héberge l'état partagé) :

```python
from easysync import SyncedObject, SyncServer, connect

server = SyncServer(port=5000)
server.start_thread()

client = connect("127.0.0.1", 5000)

@SyncedObject(client)
class GameState:
    def __init__(self):
        self.score = 0
        self.players = []

state = GameState()
state.score = 42           # propagé à tous les clients
state.players.append("A")  # propagé aussi
```

**Client** (rejoint le serveur) :

```python
from easysync import SyncedObject, connect

client = connect("192.168.1.10", 5000)

@SyncedObject(client)
class GameState:
    def __init__(self):
        self.score = 0
        self.players = []

state = GameState()
print(state.score)    # 42, mis à jour en temps réel
print(state.players)  # ['A']
```

## Architecture

Le serveur utilise `asyncio` pour gérer les connexions de manière non-bloquante, ce qui permet de supporter un grand nombre de clients simultanés sans surcharge CPU. Le client utilise un thread de réception dédié pour rester compatible avec les boucles applicatives classiques (Pygame, Matplotlib, etc.).

Le protocole réseau repose sur une sérialisation Pickle binaire encadrée par un header de 4 octets (taille du payload). Ce choix garantit une latence mesurée inférieure à 20ms.

## Fonctionnalités

- **Zéro configuration** : un décorateur `@SyncedObject` suffit.
- **Proxy transparent** : interception automatique de `__setattr__`, `__setitem__`, `append`, `pop`, etc.
- **Serveur asyncio** : basé sur `asyncio.start_server` pour une scalabilité maximale.
- **Sérialisation binaire** : protocole Pickle + framing TCP, latence < 20ms.
- **Zéro dépendance** : n'utilise que la bibliothèque standard Python.
- **Compatible Data Science** : gestion optimisée des objets NumPy, Pandas et Scikit-Learn via le pattern de ré-assignation.

## Exemples

Le dossier `examples/` contient plusieurs démonstrations :

| Fichier | Description |
|---|---|
| `pygame_example.py` | Carré synchronisé entre deux fenêtres Pygame |
| `pygame_hanoi.py` | Tours de Hanoï collaboratives |
| `numpy_matplotlib_example.py` | Streaming de données NumPy avec graphique Matplotlib |
| `pandas_example.py` | Tableur Pandas collaboratif |
| `sklearn_live_training.py` | Entraînement Scikit-Learn visible en temps réel |
| `federated_learning_example.py` | Apprentissage fédéré distribué |
| `genetic_island_example.py` | Algorithme génétique réparti (modèle des îles) |
| `tetris_ai_example.py` | IA Tetris distribuée par algorithme génétique |

Pour exécuter les exemples, installez les dépendances additionnelles :

```bash
pip install -r requirements_examples.txt
```

Puis lancez un serveur et un ou plusieurs clients :

```bash
python examples/pygame_example.py server    # Terminal 1
python examples/pygame_example.py           # Terminal 2
```

## Licence

MIT
