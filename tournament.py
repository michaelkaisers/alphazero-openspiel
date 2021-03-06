import sys
sys.path.append("/export/scratch1/home/jdw/alphazero/open_spiel")
sys.path.append("/export/scratch1/home/jdw/alphazero/open_spiel/build/python")
from game_utils import *
import logging
from train import Trainer
from torch import multiprocessing
import torch
from examplegenerator import ExampleGenerator
import copy
from network import Net

if __name__ == '__main__':
    logger = logging.getLogger('alphazero')
    multiprocessing.set_start_method('spawn')
    trainer = Trainer()
    trainer.current_net.load_state_dict(torch.load('./models/example_model_breakthrough(6x6).pth', map_location=trainer.device))

    # The following sections tests an alphaZero bot against an MCTS bot.
    # MCTS bot has 200 playouts, alphaZero has 100.
    # With the example model on 6x6 breakthrough, alphaZero should win over 99% of games.
    n_tests = 10
    generator = ExampleGenerator(trainer.current_net, trainer.name_game,
                                    trainer.device, is_test=True, generate_statistics=False)
    generator.kwargs["settings1"] = {"n_playouts": 100}
    avg_reward = generator.generate_tests(n_tests, test_zero_vs_mcts, 200)
    logger.info("alphaZero won: " + str((avg_reward*0.5+0.5)*100.) + "% of games.")


    # The following section has two alphaZero bots play against each other.
    n_tests = 1
    logger.info("n_tests: " + str(n_tests))

    agents = []
    agents.append(["AlphaZero 100 playouts", {"n_playouts": 100, "use_probabilistic_actions": True}])
    agents.append(["AlphaZero 200 playouts", {"n_playouts": 200, "use_probabilistic_actions": True}])

    logger.info(str(agents))
    generator = ExampleGenerator(trainer.current_net, trainer.name_game,
                                    trainer.device, is_test=True, generate_statistics=True)
    results = np.zeros((len(agents), len(agents)))
    for index1, agent1 in enumerate(agents):
        for index2, agent2 in enumerate(agents):
            if len(agent1) > 2:
                generator.net = copy.deepcopy(agent1[2])
                generator.net2 = copy.deepcopy(agent2[2])
                generator.net.to('cpu')
                generator.net2.to('cpu')
            generator.kwargs["settings1"], generator.kwargs["settings2"] = agent1[1], agent2[1]
            avg_reward, statistics = generator.generate_tests(n_tests, test_zero_vs_zero, None)
            results[index1, index2] = avg_reward
            logger.info(agent1[0] + " vs " + agent2[0] + ": " + str(avg_reward*0.5+0.5))
    logger.info(results)
    logger.info(results*0.5 + 0.5)
