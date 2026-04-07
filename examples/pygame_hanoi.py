import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect

try:
    import pygame
except ImportError:
    print("Error: pygame is not installed (pip install pygame)")
    sys.exit(1)


def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)

    @SyncedObject(client)
    class HanoiGame:
        def __init__(self, num_disks=5):
            self.pegs = [list(range(num_disks, 0, -1)), [], []]
            self.selected_disk = None
            self.selected_peg_index = -1

    game = HanoiGame()

    pygame.init()
    caption = "Tower of Hanoi - HOST" if is_server else "Tower of Hanoi - CLIENT"
    pygame.display.set_caption(caption)
    screen = pygame.display.set_mode((800, 600))
    clock = pygame.time.Clock()

    colors = [
        (255, 100, 100), (200, 200, 100), (100, 255, 100),
        (100, 200, 255), (200, 100, 255), (255, 100, 200),
    ]

    def draw_game():
        screen.fill((40, 45, 50))
        pygame.draw.rect(screen, (100, 70, 40), (100, 500, 600, 20))

        for i in range(3):
            x = 200 + i * 200
            pygame.draw.rect(screen, (100, 70, 40), (x - 10, 200, 20, 300))

            for j, disk_size in enumerate(game.pegs[i]):
                width = 40 + disk_size * 25
                dx = x - width // 2
                dy = 500 - (j + 1) * 25
                pygame.draw.rect(screen, colors[disk_size % len(colors)], (dx, dy, width, 25), border_radius=5)

        if game.selected_disk is not None:
            disk_size = game.selected_disk
            width = 40 + disk_size * 25
            x = 200 + game.selected_peg_index * 200
            dx = x - width // 2
            pygame.draw.rect(screen, colors[disk_size % len(colors)], (dx, 150, width, 25), border_radius=5)
            pygame.draw.rect(screen, (255, 255, 255), (dx, 150, width, 25), width=3, border_radius=5)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mx, _ = event.pos
                clicked_peg = 0 if mx < 300 else (1 if mx < 500 else 2)

                if game.selected_disk is None:
                    if len(game.pegs[clicked_peg]) > 0:
                        disk = game.pegs[clicked_peg].pop()
                        game.selected_disk = disk
                        game.selected_peg_index = clicked_peg
                else:
                    target = game.pegs[clicked_peg]
                    if len(target) == 0 or target[-1] > game.selected_disk:
                        target.append(game.selected_disk)
                        game.selected_disk = None
                        game.selected_peg_index = -1
                    else:
                        game.pegs[game.selected_peg_index].append(game.selected_disk)
                        game.selected_disk = None
                        game.selected_peg_index = -1

        draw_game()
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
