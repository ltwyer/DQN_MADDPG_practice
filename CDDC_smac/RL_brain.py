import tensorflow as tf

from bicnet import BiCNet as CommNet
# from smac.BicNet.comm_net import CommNet as CommNet
from MADDPG import DDPG
import os
import numpy as np

# ===========================
#   Actor and Critic DNNs
# ===========================

class ActorNetwork(object):
    def __init__(self, sess, learning_rate, tau, batch_size, num_agents, vector_obs_len, output_len, hidden_vector_len):
        self.sess = sess
        # self.s_dim = state_dim
        # self.a_dim = action_dim
        self.learning_rate = learning_rate
        self.tau = tau
        self.batch_size = batch_size
        self.num_agents = num_agents
        self.obs_len = vector_obs_len
        self.output_len = output_len
        self.hidden_vector_len = hidden_vector_len

        self.inputs, self.out = self.create_actor_network("actor_network")  #in: (,2,1) out(,2,1)/ (?,3,1)
        self.network_params = tf.trainable_variables()   # print('param',len(self.network_params))   8

        self.target_inputs, self.target_out = self.create_actor_network("target_actor_network")
        self.target_network_params = tf.trainable_variables()[
                                     len(self.network_params):]

        with tf.name_scope("actor_update_target_network_params"):
            self.update_target_network_params = \
                [self.target_network_params[i].assign(tf.multiply(self.network_params[i], self.tau) +
                                                      tf.multiply(self.target_network_params[i], 1. - self.tau))
                 for i in range(len(self.target_network_params))]

        self.action_gradient = tf.placeholder(tf.float32, (self.num_agents, None, self.num_agents, self.output_len), name="action_gradient")# print('action_grad',self.action_gradient) 2,n,2,1 /3,?,3,1

        with tf.name_scope("actor_gradients"):
            grads = []
            for i in range(self.num_agents):
                for j in range(self.num_agents):
                    grads.append(tf.gradients(self.out[:, j], self.network_params, -self.action_gradient[j][:, i]))# (y,x,w) y对x中每个元素求导，之后w加权
            # print("actor_grads",grads) #                = 4 [grad1,2,3,4]
            grads = np.array(grads)
            # print(grads.shape)    (4, 8)
            self.unnormalized_actor_gradients = [tf.reduce_sum(list(grads[:, i]), axis=0) for i in range(len(self.network_params))]   # len_net_param=8
            self.actor_gradients = list(map(lambda x: tf.div(x, self.batch_size), self.unnormalized_actor_gradients))  # unn(1,8)  len(actor_gradients)=8

        self.optimize = tf.train.AdamOptimizer(self.learning_rate)
        self.optimize = self.optimize.apply_gradients(zip(self.actor_gradients, self.network_params))

        self.num_trainable_vars = len(self.network_params) + len(self.target_network_params)
        self.saver = tf.train.Saver(max_to_keep=100000000)

    def create_actor_network(self, name):
        inputs = tf.placeholder(tf.float32, shape=(None, self.num_agents, self.obs_len), name="actor_inputs")
        # print('input',inputs)
        out = CommNet.actor_build_network(name, inputs, self.num_agents, self.hidden_vector_len, self.output_len)   #(, 2,1)
        return inputs, out

    def train(self, inputs, action_gradient):
        self.sess.run(self.optimize, feed_dict={
            self.inputs: inputs,
            self.action_gradient: action_gradient
        })

    def predict(self, inputs):
        return self.sess.run(self.out, feed_dict={
            self.inputs: inputs
        })

    def predict_target(self, inputs):
        return self.sess.run(self.target_out, feed_dict={
            self.target_inputs: inputs
        })

    def update_target_network(self):
        self.sess.run(self.update_target_network_params)

    def get_num_trainable_vars(self):
        return self.num_trainable_vars

    def load_model(self, model_load_steps):
        saver = tf.train.Saver()
        model_file_load = os.path.join("models/", str(model_load_steps) + "_" + "model_segment_training_actor/", "8m")
        saver.restore(self.sess, model_file_load)
        print("model trained for %s steps of agent have been loaded" % (model_load_steps))

    def save_model(self, training_steps):
        model_file_save = os.path.join("models/", str(training_steps) + "_" + "model_segment_training_actor/", "8m")
        dirname = os.path.dirname(model_file_save)
        if any(dirname):
            os.makedirs(dirname, exist_ok=True)
        self.saver.save(self.sess, model_file_save)
        print("Model trained for %s times is saved" % training_steps)


class CriticNetwork(object):
    """
    Input to the network is the state and action, output is Q(s,a).
    The action must be obtained from the output of the Actor network.

    """

    def __init__(self, sess, learning_rate, tau, num_agents, n_features, output_len, n_actions, agent_id):
        self.sess = sess
        self.learning_rate = learning_rate
        self.tau = tau
        self.num_agents = num_agents
        self.n_features = n_features
        self.output_len = output_len
        self.n_actions = n_actions
        self.agent_id = agent_id
        self.learn_step_counter = 0

        self.inputs, self.own_action, self.other_action, self.out = self.create_critic_network("critic_network")
        self.network_params = tf.trainable_variables("critic_network")

        self.target_inputs, self.target_own_action, self.target_other_action, self.target_out = self.create_critic_network("target_critic_network")
        self.target_network_params = tf.trainable_variables("target_critic_network")
        self.summary_placeholders, self.update_ops, self.summary_op, self.summary_vars, self.summary_writer = self.setup_summary()

        with tf.name_scope("critic_update_target_network_params"):
            self.update_target_network_params = \
                [self.target_network_params[i].assign(tf.multiply(self.network_params[i], self.tau)
                                                      + tf.multiply(self.target_network_params[i], 1. - self.tau))
                 for i in range(len(self.target_network_params))]

        self.predicted_q_value = tf.placeholder(tf.float16, (None, self.output_len), name="predicted_q_value")
        self.critic_loss = tf.reduce_mean(tf.squared_difference(self.predicted_q_value, self.out))
        self.optimize = tf.train.AdamOptimizer(self.learning_rate).minimize(self.critic_loss)

        self.action_grads_own = tf.gradients(self.out, self.own_action)  # out.shape:batchs,2,1; action: n,2,1;  (32,14)
        self.action_grads_other = tf.gradients(self.out, self.other_action)  # (32,98)

        self.action_grads_own = self.action_grads_own[0]   #(32, 8, 14)
        self.action_grads_other = self.action_grads_other[0]   #有可能维度不对

        self.saver = tf.train.Saver(max_to_keep=100000000)

    def create_critic_network(self, name):
        inputs = tf.placeholder(tf.float16, shape=(None, self.n_features), name="critic_inputs")
        own_action = tf.placeholder(tf.float16, shape=(None, self.n_actions), name="critic_action")
        other_action = tf.placeholder(tf.float16, shape=(None, self.n_actions * (self.num_agents-1)), name="critic_other_action")

        out = DDPG.critic_build_network(name, inputs, self.n_features, self.num_agents, self.n_actions, own_action, other_action)
        return inputs, own_action, other_action, out

    def train(self, inputs, action, other_action, predicted_q_value):
        self.learn_step_counter += 1
        return self.sess.run([self.out, self.critic_loss, self.optimize], feed_dict={
            self.inputs: inputs,
            self.own_action: action,
            self.other_action: other_action,
            self.predicted_q_value: predicted_q_value
        })

    def predict(self, inputs, action, other_action):
        return self.sess.run(self.out, feed_dict={
            self.inputs: inputs,
            self.own_action: action,
            self.other_action: other_action
        })

    def get_params(self):
        return self.sess.run(self.network_params)

    def predict_target(self, inputs, action, other_action):

        return self.sess.run(self.target_out, feed_dict={
            self.target_inputs: inputs,
            self.target_own_action: action,
            self.target_other_action: other_action
        })

    def action_gradients(self, inputs, action, other_action):
        return self.sess.run([self.out, self.action_grads_own, self.action_grads_other], feed_dict={
            self.inputs: inputs,
            self.own_action: action,
            self.other_action: other_action,
        })

    def update_target_network(self):
        self.sess.run(self.update_target_network_params)


    def setup_summary(self):
        fileWritePath = os.path.join("logs/", "agent_No_" + str(self.agent_id) + "/")
        summary_writer = tf.summary.FileWriter(fileWritePath, self.sess.graph)
        training_step = tf.Variable(0., dtype=tf.float16)
        critic_cost = tf.Variable(0., dtype=tf.float16)
        eps_rew_agent = tf.Variable(0., dtype=tf.float16)
        eps_rew_all = tf.Variable(0., dtype=tf.float16)
        tf.summary.scalar("training_step", training_step)
        tf.summary.scalar("critic_cost", critic_cost)
        tf.summary.scalar("eps_rew_agent", eps_rew_agent)
        tf.summary.scalar("eps_rew_all", eps_rew_all)
        summary_vars = [training_step, critic_cost, eps_rew_agent, eps_rew_all]

        summary_placeholders = [tf.placeholder(tf.float16) for _ in range(len(summary_vars))]

        update_ops = [summary_vars[i].assign(summary_placeholders[i]) for i in range(len(summary_vars))]
        summary_op = tf.summary.merge_all()

        return summary_placeholders, update_ops, summary_op, summary_vars, summary_writer

    def plotting(self):
        tensorboard_info = [self.learn_step_counter, self.critic_cost, self.episode_rew_agent, self.episode_rew_all]
        vars_plot = []
        for i in range(len(tensorboard_info)):
            vars_plot.append(self.sess.run(self.update_ops[i], feed_dict={self.summary_placeholders[i]: float(tensorboard_info[i])}))

        summary_0 = tf.Summary(value=[tf.Summary.Value(tag="training_step", simple_value=vars_plot[0])])
        summary_1 = tf.Summary(value=[tf.Summary.Value(tag="critic_cost", simple_value=vars_plot[1])])
        summary_2 = tf.Summary(value=[tf.Summary.Value(tag="eps_rew_agent", simple_value=vars_plot[2])])
        summary_3 = tf.Summary(value=[tf.Summary.Value(tag="eps_rew_all", simple_value=vars_plot[3])])

        self.summary_writer.add_summary(summary_0, self.episode)
        self.summary_writer.add_summary(summary_1, self.learn_step_counter)
        self.summary_writer.add_summary(summary_2, self.episode)
        self.summary_writer.add_summary(summary_3, self.episode)

    def get_episode_reward(self, eps_r_agent, eps_r_all, episode):
        self.episode_rew_agent = eps_r_agent
        self.episode_rew_all = eps_r_all
        self.episode = episode

    def get_critic_loss(self, critic_cost):
        self.critic_cost = critic_cost
        self.plotting()

    def save_model(self, training_steps):
        model_file_save = os.path.join("models/", "agent_No_" + str(self.agent_id) + "/",
                                       str(training_steps) + "_" + "model_segment_training_critic/", "8m")
        dirname = os.path.dirname(model_file_save)
        if any(dirname):
            os.makedirs(dirname, exist_ok=True)
        self.saver.save(self.sess, model_file_save)
        print("Model trained for %s times is saved" % training_steps)

    def load_model(self, model_load_steps):
        saver = tf.train.Saver()
        model_file_load = os.path.join("models/", "agent_No_" + str(self.agent_id) + "/",
                                       str(model_load_steps) + "_" + "model_segment_training_critic/", "8m")
        saver.restore(self.sess, model_file_load)
        print("model trained for %s steps of agent %s have been loaded" % (model_load_steps, self.agent_id))


