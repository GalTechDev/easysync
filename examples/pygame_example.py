import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect

try:
    import pygame
except ImportError:
    print("Erreur : pygame n'est pas installé (pip install pygame)")
    sys.exit(1)


def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)

    @SyncedObject(client)
    class SharedSquare:
        def __init__(self):
            self.x = 320
            self.y = 240

    square = SharedSquare()

    pygame.init()
    if is_server:
        pygame.display.set_caption("EasySync (HOTE)")
        color = (255, 50, 50)
    else:
        pygame.display.set_caption("EasySync (CLIENT)")
        color = (50, 50, 255)

    screen = pygame.display.set_mode((640, 480))
    clock = pygame.time.Clock()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.MOUSEMOTION:
                if pygame.mouse.get_focused():
                    square.x, square.y = event.pos

        screen.fill((30, 30, 30))
        pygame.draw.rect(screen, color, (square.x - 25, square.y - 25, 50, 50))
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
