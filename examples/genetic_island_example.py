import pygame
import string
import sys
import os
import random
import uuid
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from easysync import SyncedObject, SyncServer, connect

TARGET_DNA = "EASYSYNC DISTRIBUTED GENETICS"
ALPHABET = string.ascii_uppercase + " "


def generate_random_dna():
    return "".join(random.choice(ALPHABET) for _ in range(len(TARGET_DNA)))


def calculate_fitness(dna):
    return sum(1 for a, b in zip(dna, TARGET_DNA) if a == b)


def main():
    is_server = len(sys.argv) > 1 and sys.argv[1] == "server"

    if is_server:
        server = SyncServer(port=5000)
        server.start_thread()
        time.sleep(0.5)

    client = connect("127.0.0.1", 5000)
    worker_id = f"Island_{uuid.uuid4().hex[:4]}" if not is_server else "Orchestrator"

    @SyncedObject(client)
    class GeneticWorld:
        def __init__(self):
            self.world_champions = []
            self.epoch = 0
            self.client_champions = {}

    world = GeneticWorld()

    pygame.init()
    sw, sh = 800, 600
    screen = pygame.display.set_mode((sw, sh))
    pygame.display.set_caption(f"Genetic Model — {worker_id}")
    clock = pygame.time.Clock()

    font_large = pygame.font.SysFont("monospace", 32, bold=True)
    font_small = pygame.font.SysFont("monospace", 18)

    def draw_dna(surface, dna, y_pos, title=""):
        cx = (sw - len(TARGET_DNA) * 18) // 2
        if title:
            surface.blit(font_small.render(title, True, (200, 200, 200)), (cx, y_pos - 25))
        for i, char in enumerate(dna):
            color = (50, 255, 50) if char == TARGET_DNA[i] else (255, 50, 50)
            surface.blit(font_large.render(char, True, color), (cx, y_pos))
            cx += 18

    running = True

    if is_server:
        last_exchange = time.time()

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            if time.time() - last_exchange > 2.0:
                if len(world.client_champions) > 0:
                    champs = sorted(world.client_champions.values(), key=lambda x: x[1], reverse=True)
                    world.world_champions = champs[:3]
                    world.client_champions.clear()
                    world.epoch += 1
                last_exchange = time.time()

            screen.fill((30, 30, 45))
            title = font_large.render("GLOBAL ORCHESTRATOR", True, (255, 200, 0))
            screen.blit(title, (sw // 2 - title.get_width() // 2, 30))
            info = font_small.render(f"Connected islands: {len(world.client_champions)}", True, (200, 200, 200))
            screen.blit(info, (sw // 2 - info.get_width() // 2, 80))
            info2 = font_small.render(f"Global epoch: {world.epoch}", True, (255, 100, 255))
            screen.blit(info2, (sw // 2 - info2.get_width() // 2, 110))

            if world.world_champions:
                for idx, champ in enumerate(world.world_champions):
                    pct = int((champ[1] / len(TARGET_DNA)) * 100)
                    draw_dna(screen, champ[0], 250 + idx * 100, f"Top {idx + 1} ({pct}%)")

            pygame.display.flip()
            clock.tick(30)
    else:
        pop_size = 150
        population = [generate_random_dna() for _ in range(pop_size)]
        last_epoch = -1
        generation = 0
        best_dna = population[0]
        best_score = 0

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            if world.epoch > last_epoch:
                for dna, _ in world.world_champions:
                    population[random.randint(0, pop_size - 1)] = dna
                last_epoch = world.epoch

            for _ in range(10):
                fitnesses = [(d, calculate_fitness(d)) for d in population]
                fitnesses.sort(key=lambda x: x[1], reverse=True)
                best_dna, best_score = fitnesses[0]

                if generation % 20 == 0:
                    tmp = world.client_champions.copy()
                    tmp[worker_id] = (best_dna, best_score)
                    world.client_champions = tmp

                new_pop = [best_dna]
                while len(new_pop) < pop_size:
                    p1 = random.choice(fitnesses[:30])[0]
                    p2 = random.choice(fitnesses[:30])[0]
                    split = random.randint(0, len(TARGET_DNA) - 1)
                    child = list(p1[:split] + p2[split:])
                    for i in range(len(child)):
                        if random.random() < 0.05:
                            child[i] = random.choice(ALPHABET)
                    new_pop.append("".join(child))

                population = new_pop
                generation += 1

            screen.fill((20, 40, 25))
            title = font_large.render(f"ISLAND: {worker_id}", True, (100, 255, 100))
            screen.blit(title, (sw // 2 - title.get_width() // 2, 30))
            gen = font_small.render(f"Local generation: {generation}", True, (200, 255, 200))
            screen.blit(gen, (sw // 2 - gen.get_width() // 2, 80))

            target_surf = font_small.render("Target: " + TARGET_DNA, True, (80, 100, 80))
            screen.blit(target_surf, (sw // 2 - target_surf.get_width() // 2, 200))
            pct = int((best_score / len(TARGET_DNA)) * 100)
            draw_dna(screen, best_dna, 250, f"Best specimen ({pct}%)")

            pygame.display.flip()
            clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
