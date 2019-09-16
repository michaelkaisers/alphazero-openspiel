import torch
import torch.nn as nn
import numpy as np
import time
import pickle
from datetime import datetime
import pyspiel
from open_spiel.python.algorithms import mcts

from examplegenerator import ExampleGenerator
from connect4net import Net
from mctsagent import MCTSAgent
from alphazerobot import AlphaZeroBot, NeuralNetBot


class Trainer:
    def __init__(self):
        self.name = "openspieltest"
        self.model_path = "models/"
        self.start_time = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        self.save = True
        self.save_n_gens = 5
        self.board_width = 7
        self.board_height = 6
        self.n_in_row = 4
        self.n_games_per_generation = 250
        self.batches_per_generation = 1000
        self.n_games_buffer = 2500
        self.buffer = []
        self.n_tests = 25
        self.use_gpu = True
        self.batch_size = 32
        self.lr = 0.0002
        self.games_played = 0
        self.criterion_policy = nn.BCELoss()
        self.criterion_value = nn.MSELoss()
        self.test_data = {'games_played': [], 'zero_vs_random': [], 'zero_vs_mcts100': [], 'zero_vs_mcts200': [],
                          'net_vs_random': [], 'net_vs_mcts100': [], 'net_vs_mcts200': []}
        # @todo clean cuda code up
        if self.use_gpu:
            if not torch.cuda.is_available():
                print("Tried to use GPU, but none is available")
                self.use_gpu = False

        self.device = torch.device("cuda:0" if self.use_gpu else "cpu")

        self.current_net = Net(width=self.board_width, height=self.board_height, device=self.device)
        self.current_net.to(self.device)

        self.current_agent = MCTSAgent(self.current_net.predict,
                                       board_width=self.board_width,
                                       board_height=self.board_height,
                                       n_in_row=self.n_in_row,
                                       use_gpu=self.use_gpu)
        self.optimizer = torch.optim.Adam(self.current_net.parameters(), lr=self.lr, weight_decay=0.0001)

    def net_step(self, flattened_buffer):
        """Samples a random batch and updates the NN parameters with this bat

        @return:
        """
        self.current_net.zero_grad()

        # Select samples and format them to use as batch
        sample_ids = np.random.randint(len(flattened_buffer), size=self.batch_size)
        x = [flattened_buffer[i][1] for i in sample_ids]
        p_r = [flattened_buffer[i][2] for i in sample_ids]
        v_r = [flattened_buffer[i][3] for i in sample_ids]

        x = torch.from_numpy(np.array(x)).float().to(self.device)
        p_r = torch.tensor(np.array(p_r)).float().to(self.device)
        v_r = torch.tensor(np.array(v_r)).float().to(self.device)

        # Pass through network
        p_t, v_t = self.current_net(x)

        # Backward pass
        loss_v = self.criterion_value(v_t, v_r.unsqueeze(1))
        loss_p = self.criterion_policy(p_t, p_r)
        loss = loss_v + loss_p
        loss.backward()
        self.optimizer.step()
        return loss_p, loss_v

    def train_network(self, n_batches):
        """Trains the neural network for batches_per_generation batches

        @return:
        """
        print("Training Network")
        self.current_net.train()
        flattened_buffer = [sample for game in self.buffer for sample in game]
        print("Removing duplciates")
        print("Initial amount of samples: " + str(len(flattened_buffer)))
        start = time.time()
        # Remove duplicates
        flattened_buffer_dict = dict()
        flattened_buffer_counts = dict()
        for item in flattened_buffer:
            if item[0] in flattened_buffer_dict:
                # Average policy
                flattened_buffer_dict[item[0]][2] = [sum(x) for x in zip(flattened_buffer_dict[item[0]][2], item[2])]
                # Average value
                flattened_buffer_dict[item[0]][3] += item[3]
                flattened_buffer_counts[item[0]] += 1

            else:
                flattened_buffer_dict[item[0]] = item
                flattened_buffer_counts[item[0]] = 1

        for key, value in flattened_buffer_dict.items():
            flattened_buffer_dict[key][2] = [x / flattened_buffer_counts[key] for x in flattened_buffer_dict[key][2]]
            flattened_buffer_dict[key][3] = flattened_buffer_dict[key][3] / flattened_buffer_counts[key]

        flattened_buffer = list(flattened_buffer_dict.values())
        print("New amount of samples: " + str(len(flattened_buffer)))
        print("Duplication removal took:" + str(time.time() - start) + "seconds")
        loss_tot_v = 0
        loss_tot_p = 0

        for i in range(n_batches):
            loss_p, loss_v = self.net_step(flattened_buffer)
            loss_tot_p += loss_p
            loss_tot_v += loss_v
            if i % 200 == 0:
                print("Batch: " + str(i) + "Loss policy: " + str(loss_tot_p / 200.) + "Loss value: " + str(
                    loss_tot_v / 200.))
                loss_tot_v = 0
                loss_tot_p = 0
        self.current_net.eval()

    def generate_examples(self, n_games):
        """Generates games in a multithreaded way.

        @param n_games:
        @return:
        """
        # Generate new training samples
        # print("Generating Data")
        # start = time.time()
        # for i in range(n_games):
        # 	print("Game " + str(i) + " / " + str(n_games))
        # 	examples = Examplegenerator.play_game_self(self.current_net.predict)
        # 	self.buffer.append(examples)
        # print("Finished Generating Data (normal)")
        # print(time.time()-start)

        start = time.time()

        # Generate the examples
        generator = ExampleGenerator(self.current_net, board_width=self.board_width,
                                     board_height=self.board_height,
                                     n_in_row=self.n_in_row,
                                     use_gpu=self.use_gpu)
        games = generator.generate_examples(n_games)
        self.games_played += self.n_games_per_generation

        # Add examples to buffer
        for examples in games:
            self.buffer.append(examples)
        print("Finished Generating Data (threaded). Took: " + str(time.time() - start) + " seconds")

        # Remove oldest entries from buffer if too long
        if len(self.buffer) > self.n_games_buffer:
            print("Buffer full. Deleting oldest samples.")
            while len(self.buffer) > self.n_games_buffer:
                del self.buffer[0]

    def test_zero_vs_mcts(self, max_search_nodes):
        game = pyspiel.load_game('connect_four')

        # Alphazero first
        zero_bot = AlphaZeroBot(game, 0, policy_fn=self.current_net.predict, use_dirichlet=False)
        mcts_bot = mcts.MCTSBot(game, 1, 1,
                                max_search_nodes, mcts.RandomRolloutEvaluator(1))
        score1 = play_game(game, zero_bot, mcts_bot)

        # Random bot first
        zero_bot = AlphaZeroBot(game, 1, policy_fn=self.current_net.predict, use_dirichlet=False)
        mcts_bot = mcts.MCTSBot(game, 0, 1,
                                max_search_nodes, mcts.RandomRolloutEvaluator(1))
        score2 = -play_game(game, mcts_bot, zero_bot)
        return score1, score2

    def test_net_vs_mcts(self, max_search_nodes):
        game = pyspiel.load_game('connect_four')

        # Alphazero first
        zero_bot = NeuralNetBot(game, 0, self.current_net)
        mcts_bot = mcts.MCTSBot(game, 1, 1,
                                max_search_nodes, mcts.RandomRolloutEvaluator(1))
        score1 = play_game(game, zero_bot, mcts_bot)

        # Random bot first
        zero_bot = NeuralNetBot(game, 1, self.current_net)
        mcts_bot = mcts.MCTSBot(game, 0, 1,
                                max_search_nodes, mcts.RandomRolloutEvaluator(1))
        score2 = -play_game(game, mcts_bot, zero_bot)
        return score1, score2

    def test_zero_vs_random(self):
        game = pyspiel.load_game('connect_four')

        # Alphazero first
        zero_bot = AlphaZeroBot(game, 0, policy_fn=self.current_net.predict, use_dirichlet=False)
        random_bot = pyspiel.make_uniform_random_bot(game, 1, np.random.randint(0, 1000))
        score1 = play_game(game, zero_bot, random_bot)

        # Random bot first
        zero_bot = AlphaZeroBot(game, 1, policy_fn=self.current_net.predict, use_dirichlet=False)
        random_bot = pyspiel.make_uniform_random_bot(game, 0, np.random.randint(0, 1000))
        score2 = -play_game(game, random_bot, zero_bot)
        return score1, score2

    def test_net_vs_random(self):
        game = pyspiel.load_game('connect_four')

        # Alphazero first
        zero_bot = NeuralNetBot(game, 0, self.current_net)
        random_bot = pyspiel.make_uniform_random_bot(game, 1, np.random.randint(0, 1000))
        score1 = play_game(game, zero_bot, random_bot)

        # Random bot first
        zero_bot = NeuralNetBot(game, 1, self.current_net)
        random_bot = pyspiel.make_uniform_random_bot(game, 0, np.random.randint(0, 1000))
        score2 = -play_game(game, random_bot, zero_bot)
        return score1, score2

    def test_agent(self):
        start = time.time()
        print("Testing...")
        self.test_data['games_played'].append(self.games_played)
        score_tot = 0.
        for i in range(self.n_tests):
            score1, score2 = self.test_zero_vs_random()
            score_tot += score1
            score_tot += score2
        avg = score_tot / (2 * self.n_tests)
        self.test_data['zero_vs_random'].append(avg)
        print("Average score vs random:" + str(avg))
        score_tot = 0.
        for i in range(self.n_tests):
            score1, score2 = self.test_net_vs_random()
            score_tot += score1
            score_tot += score2
        avg = score_tot / (2 * self.n_tests)
        self.test_data['net_vs_random'].append(avg)
        print("Average score vs random (net only):" + str(avg))
        score_tot = 0.
        for i in range(self.n_tests):
            score1, score2 = self.test_zero_vs_mcts(100)
            score_tot += score1
            score_tot += score2
        avg = score_tot / (2 * self.n_tests)
        self.test_data['zero_vs_mcts100'].append(avg)
        print("Average score vs mcts100:" + str(avg))
        score_tot = 0.
        for i in range(self.n_tests):
            score1, score2 = self.test_net_vs_mcts(100)
            score_tot += score1
            score_tot += score2
        avg = score_tot / (2 * self.n_tests)
        self.test_data['net_vs_mcts100'].append(avg)
        print("Average score vs mcts100 (net only):" + str(avg))
        score_tot = 0.
        for i in range(self.n_tests):
            score1, score2 = self.test_zero_vs_mcts(200)
            score_tot += score1
            score_tot += score2
        avg = score_tot / (2 * self.n_tests)
        self.test_data['zero_vs_mcts200'].append(avg)
        print("Average score vs mcts200:" + str(avg))
        score_tot = 0.
        for i in range(self.n_tests):
            score1, score2 = self.test_net_vs_mcts(200)
            score_tot += score1
            score_tot += score2
        avg = score_tot / (2 * self.n_tests)
        self.test_data['net_vs_mcts200'].append(avg)
        print("Average score vs mcts200 (net only):" + str(avg))
        with open("logs/" + self.start_time + str(self.name) + ".p", 'wb') as f:
            pickle.dump(self.test_data, f)
        print("Testing took: " + str(time.time() - start) + "seconds")
        return

    def run(self):
        self.current_net.eval()
        self.test_agent()
        generation = 0
        while True:
            generation += 1
            self.generate_examples(self.n_games_per_generation)
            self.train_network(self.batches_per_generation)
            self.test_agent()

            # Periodically save network
            if self.save and generation % self.save_n_gens == 0:
                print("Saving network")
                torch.save(self.current_net.state_dict(), self.model_path + self.name + str(generation) + ".pth")
                print("Network saved")


def play_game(game, player1, player2):
    # Returns the reward of the first player
    state = game.new_initial_state()
    while not state.is_terminal():
        if len(state.history()) % 2 == 0:
            _, action = player1.step(state)
        else:
            _, action = player2.step(state)
        state.apply_action(action)
    return state.returns()[0]


if __name__ == '__main__':
    trainer = Trainer()
    trainer.run()
