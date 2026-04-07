import sys
import os
import time
import uuid
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect
import pygame

# Tetris

SHAPES = [
    [[1, 1, 1, 1]],
    [[1, 1], [1, 1]],
    [[0, 1, 0], [1, 1, 1]],
    [[1, 0, 0], [1, 1, 1]],
    [[0, 0, 1], [1, 1, 1]],
    [[0, 1, 1], [1, 1, 0]],
    [[1, 1, 0], [0, 1, 1]],
]

COLORS = [
    (0, 255, 255), (255, 255, 0), (128, 0, 128),
    (0, 0, 255), (255, 165, 0), (0, 255, 0), (255, 0, 0),
]


def rotate_shape(shape):
    return [list(row) for row in zip(*shape[::-1])]


class TetrisEngine:
    def __init__(self):
        self.rows = 20
        self.cols = 10
        self.board = [[0] * self.cols for _ in range(self.rows)]
        self.lines = 0
        self.game_over = False
        self._new_piece()

    def _new_piece(self):
        self.shape_idx = random.randint(0, 6)
        self.shape = [list(r) for r in SHAPES[self.shape_idx]]
        self.shape_x = self.cols // 2 - len(self.shape[0]) // 2
        self.shape_y = 0
        if not self.valid_position(self.shape, self.shape_x, self.shape_y):
            self.game_over = True

    def _clone(self):
        cl = TetrisEngine()
        cl.board = [list(r) for r in self.board]
        cl.lines = self.lines
        cl.shape_idx = self.shape_idx
        cl.shape = [list(r) for r in self.shape]
        return cl

    def valid_position(self, shape, ox, oy):
        for y, row in enumerate(shape):
            for x, cell in enumerate(row):
                if cell:
                    nx, ny = x + ox, y + oy
                    if nx < 0 or nx >= self.cols or ny >= self.rows:
                        return False
                    if ny >= 0 and self.board[ny][nx]:
                        return False
        return True

    def place_shape(self, shape, ox, oy):
        for y, row in enumerate(shape):
            for x, cell in enumerate(row):
                if cell:
                    self.board[y + oy][x + ox] = self.shape_idx + 1

    def clear_lines(self):
        new_board = [row for row in self.board if not all(row)]
        cleared = self.rows - len(new_board)
        self.board = [[0] * self.cols for _ in range(cleared)] + new_board
        self.lines += cleared
        return cleared


# AI

def get_board_metrics(board):
    holes, bumpiness = 0, 0
    heights = [0] * 10

    for x in range(10):
        found = False
        for y in range(20):
            if board[y][x]:
                if not found:
                    heights[x] = 20 - y
                    found = True
            elif found:
                holes += 1

    for x in range(9):
        bumpiness += abs(heights[x] - heights[x + 1])

    return holes, bumpiness, sum(heights)


def ai_best_move(engine, weights):
    best_score = -float("inf")
    best_move = None
    shape = engine.shape

    for _ in range(4):
        for x in range(10):
            if engine.valid_position(shape, x, 0):
                sim = engine._clone()
                y = 0
                while sim.valid_position(shape, x, y + 1):
                    y += 1

                sim.place_shape(shape, x, y)
                cleared = sim.clear_lines()
                holes, bump, height = get_board_metrics(sim.board)
                score = weights[0] * cleared + weights[1] * holes + weights[2] * bump + weights[3] * height

                if score > best_score:
                    best_score = score
                    best_move = ([list(r) for r in shape], x, y)

        shape = rotate_shape(shape)

    return best_move


def random_weights():
    return [random.uniform(-1, 1) for _ in range(4)]


# Network

def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)
    worker_id = f"Robot_{uuid.uuid4().hex[:4]}" if not is_server else "Orchestrator"

    @SyncedObject(client)
    class TetrisWorld:
        def __init__(self):
            self.epoch = 0
            self.world_champions = []
            self.client_champions = {}

    world = TetrisWorld()

    pygame.init()
    screen = pygame.display.set_mode((700, 650))
    pygame.display.set_caption(f"Tetris AI — {worker_id}")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("monospace", 18, bold=True)
    font_lg = pygame.font.SysFont("monospace", 32, bold=True)

    def draw_board(surface, engine, ox, oy):
        pygame.draw.rect(surface, (50, 50, 50), (ox, oy, 300, 600), 2)
        for y in range(20):
            for x in range(10):
                val = engine.board[y][x]
                if val:
                    pygame.draw.rect(surface, COLORS[val - 1], (ox + x * 30, oy + y * 30, 30, 30))
                    pygame.draw.rect(surface, (0, 0, 0), (ox + x * 30, oy + y * 30, 30, 30), 1)

    running = True

    if is_server:
        last_exchange = time.time()

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            if time.time() - last_exchange > 3.0:
                if len(world.client_champions) > 0:
                    champs = sorted(world.client_champions.values(), key=lambda x: x[1], reverse=True)
                    world.world_champions = champs[:3]
                    world.client_champions.clear()
                    world.epoch += 1
                last_exchange = time.time()

            screen.fill((30, 30, 45))
            screen.blit(font_lg.render("CENTRAL TETRIS SERVER", True, (255, 200, 0)), (20, 20))
            screen.blit(font.render(f"Connected islands: {len(world.client_champions)}", True, (200, 200, 200)), (20, 80))
            screen.blit(font.render(f"Global epoch: {world.epoch}", True, (255, 100, 255)), (20, 110))

            if world.world_champions:
                for idx, champ in enumerate(world.world_champions):
                    w = champ[0]
                    screen.blit(font.render(f"Top {idx + 1} | Record: {champ[1]} lines", True, (100, 255, 100)), (20, 200 + idx * 80))
                    screen.blit(font.render(f"L={w[0]:.2f} H={w[1]:.2f} B={w[2]:.2f} A={w[3]:.2f}", True, (200, 200, 200)), (20, 225 + idx * 80))

            pygame.display.flip()
            clock.tick(30)
    else:
        pop_size = 10
        population = [random_weights() for _ in range(pop_size)]
        last_epoch = -1
        generation = 0
        game = TetrisEngine()
        ai_idx = 0
        best_score = 0
        best_weights = population[0]

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            if world.epoch > last_epoch:
                for w, _ in world.world_champions:
                    population[random.randint(0, pop_size - 1)] = w
                last_epoch = world.epoch

            weights = population[ai_idx]
            move = ai_best_move(game, weights)
            if move:
                s, bx, by = move
                game.shape = s
                game.place_shape(game.shape, bx, by)
                game.clear_lines()
                game._new_piece()
            else:
                game.game_over = True

            if game.game_over:
                if game.lines >= best_score:
                    best_score = game.lines
                    best_weights = weights

                ai_idx += 1
                game = TetrisEngine()

                if ai_idx >= pop_size:
                    tmp = world.client_champions.copy()
                    tmp[worker_id] = (best_weights, best_score)
                    world.client_champions = tmp

                    new_pop = [best_weights]
                    while len(new_pop) < pop_size:
                        parent = random.choice(population)
                        child = [g + random.uniform(-0.2, 0.2) for g in parent]
                        new_pop.append(child)
                    population = new_pop
                    ai_idx = 0
                    generation += 1

            screen.fill((20, 40, 25))
            screen.blit(font_lg.render(f"TETRIS ROBOT: {worker_id}", True, (100, 255, 100)), (10, 10))
            screen.blit(font.render(f"Local generation: {generation}", True, (200, 200, 200)), (10, 60))
            screen.blit(font.render(f"AI #{ai_idx + 1}/{pop_size}", True, (200, 200, 200)), (10, 85))
            screen.blit(font.render(f"Lines (this game): {game.lines}", True, (255, 100, 100)), (10, 130))
            screen.blit(font.render(f"All-time best: {best_score}", True, (255, 200, 0)), (10, 160))
            draw_board(screen, game, 350, 40)

            pygame.display.flip()
            clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
