import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect

try:
    import pandas as pd
except ImportError:
    print("Erreur : installez pandas (pip install pandas)")
    sys.exit(1)


def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)

    @SyncedObject(client)
    class Spreadsheet:
        def __init__(self):
            self.df = pd.DataFrame({
                "Employé": ["Alice", "Bob", "Charlie"],
                "Département": ["Ventes", "Tech", "Tech"],
                "Salaire": [3500, 4200, 4100],
                "Validé": [False, False, False],
            })

    sheet = Spreadsheet()
    time.sleep(1)

    if is_server:
        while True:
            try:
                cmd = input("\nIndex de l'employé à valider (0/1/2, q=quitter) : ")
                if cmd.lower() == "q":
                    break

                idx = int(cmd)
                if idx in (0, 1, 2):
                    tmp = sheet.df.copy()
                    tmp.loc[idx, "Validé"] = True
                    tmp.loc[idx, "Salaire"] += 100
                    sheet.df = tmp
                    print(sheet.df)
            except ValueError:
                pass
    else:
        last_str = ""
        while True:
            current = sheet.df.to_string()
            if current != last_str:
                print(f"\n>>> Mise à jour reçue :\n{current}")
                last_str = current
            time.sleep(0.5)


if __name__ == "__main__":
    main()
