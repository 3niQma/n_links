"""
A Box2D environment of a planar manipulator with an arbitrary number of links n.

The environment can be solved by training RL agents (e.g., Stable Baselines https://github.com/hill-a/stable-baselines)

Note that this environment was created by adapting the acrobot-v1 environment (https://gym.openai.com/envs/Acrobot-v1/) from A. Geramifard, C. Dann, RH. Klein, W. Dabney, J. How, "RLPy: A Value-Function-Based Reinforcement Learning Framework for Education and Research." JMLR, 2015.
"""

import time
import Box2D
import gym
from Box2D.b2 import (circleShape, polygonShape, fixtureDef, revoluteJointDef, weldJointDef)
from gym import spaces
from gym.utils import seeding, EzPickle
from Utils.helper_fcts import *
from gym.envs.classic_control import rendering

# Slow rendering with output
DEBUG = False
FPS = 50  # Frames per Second


class NLinksBox2DEnv(gym.Env, EzPickle):
    metadata = {
        'render.modes': ['human', 'rgb_array'],
        'video.frames_per_second': FPS
    }

    SCALE = 70.0
    VIEWPORT_W, VIEWPORT_H = 800, 800

    # Punishing energy (integral of applied forces); huge impact on performance
    POWER = False

    LINK_MASS = 1.0  # kg
    LINK_HEIGHT = 0.5  # m
    LINK_WIDTH = 0.10  # m

    MAX_TIME_STEP = FPS * 5  # Maximum length of an episode

    # Min distance for end effector target
    MIN_DISTANCE = 0.025  # m

    MAX_JOINT_VEL = 3.0

    GRAVITY = -9.81  # m/s^2

    FIX_CIRCLE = fixtureDef(
        shape=circleShape(radius=LINK_WIDTH),
        density=1e-3,
        friction=0.0,
        restitution=0.0,
        categoryBits=0x0020,
        maskBits=0x001)

    FIX_POLY = fixtureDef(
        shape=polygonShape(box=(LINK_WIDTH / 2, LINK_HEIGHT / 2)),
        density=LINK_MASS / (LINK_WIDTH * LINK_HEIGHT),
        friction=0.0,
        restitution=0.0,
        categoryBits=0x0020,
        maskBits=0x001)

    time_step = n_links = np_random = world = viewer = None
    draw_list = []

    # The Box2D objects below need to be destroyed when resetting world to release allocated memory
    anchor = None  # static body to hold the manipulator base fixed
    target = None  # static body to represent the target position
    end_effector = None  # dynamic body of end effector
    links = []  # dynamic body of links
    joint_bodies = []  # dynamic body of joints
    joint_fixes = []  # weld joints to connect previous link to the next joint_body
    joints = []  # revolute joints to connect previous joint_body to the next link

    def __init__(self, n_links):

        self.COLOR_ANCHOR = None
        self.COLOR_JOINTS = None
        self.COLOR_BORDER = None
        self.COLOR_LINKS = None
        self.COLOR_EE = None
        self.COLOR_TARGET = None
        self.COLOR_BACKGROUND = None

        self.MAX_JOINT_TORQUE = None  # 80

        self.ANCHOR_X = None
        self.ANCHOR_Y = None

        self.n_links = n_links

        EzPickle.__init__(self)
        self.seed()

        self.world = Box2D.b2World(gravity=(0, self.GRAVITY))

        len_arms = self.n_links * self.LINK_HEIGHT
        # x,y observation space is twice big to include anchor deviations from (0,0)
        high = [len_arms * 2.0, len_arms * 2.0, len_arms * 2.0, len_arms * 2.0]
        for i in range(self.n_links):
            high.append(np.pi)
            high.append(self.MAX_JOINT_VEL)
        high = np.array(high)
        self.action_space = spaces.Box(-np.ones(self.n_links), np.ones(self.n_links), dtype=np.float32)
        self.observation_space = spaces.Box(-high, high, dtype=np.float32)

        self.reset()

    def _destroy(self):

        for i in range(len(self.joints)):
            self.world.DestroyJoint(self.joints[i])
        self.joints = []

        for i in range(len(self.joint_fixes)):
            self.world.DestroyJoint(self.joint_fixes[i])
        self.joint_fixes = []

        for i in range(len(self.joint_bodies)):
            self.world.DestroyBody(self.joint_bodies[i])
        self.joint_bodies = []

        for i in range(len(self.links)):
            self.world.DestroyBody(self.links[i])
        self.links = []

        if self.end_effector:
            self.world.DestroyBody(self.end_effector)
        self.end_effector = None

        if self.target:
            self.world.DestroyBody(self.target)
        self.target = None

        if self.anchor:
            self.world.DestroyBody(self.anchor)
        self.anchor = None

    def _create_anchor(self):
        self.anchor = self.world.CreateStaticBody(position=(self.ANCHOR_X, self.ANCHOR_Y), fixtures=self.FIX_CIRCLE)
        self.anchor.color1 = self.COLOR_ANCHOR
        self.anchor.color2 = self.COLOR_BORDER
        # print(f"anchor mass:         {self.anchor.mass}")
        # print(f"anchor inertia:      {self.anchor.inertia}")
        # print(f"anchor local center: {self.anchor.localCenter}")
        # print(f"anchor world center: {self.anchor.worldCenter}")

    def _create_first_arm(self):
        self.joint_bodies.append(
            self.world.CreateDynamicBody(position=self.anchor.position, fixtures=self.FIX_CIRCLE))
        self.joint_bodies[0].color1 = self.COLOR_JOINTS
        self.joint_bodies[0].color2 = self.COLOR_BORDER

        rjd = weldJointDef(bodyA=self.anchor, bodyB=self.joint_bodies[0],
                           localAnchorA=(0.0, 0.0), localAnchorB=(0.0, 0.0))
        self.joint_fixes.append(self.world.CreateJoint(rjd))

        self.links.append(
            self.world.CreateDynamicBody(position=self.joint_bodies[0].position - (0.0, self.LINK_HEIGHT / 2),
                                         angle=0.0, fixtures=self.FIX_POLY))
        self.links[0].color1 = self.COLOR_LINKS
        self.links[0].color2 = self.COLOR_BORDER
        # print(f"link1 mass:         {self.links[0].mass}")
        # print(f"link1 inertia:      {self.links[0].inertia}")
        # print(f"link1 local center: {self.links[0].localCenter}")
        # print(f"link1 world center: {self.links[0].worldCenter}")

        rjd = revoluteJointDef(bodyA=self.anchor, bodyB=self.links[0], localAnchorA=(0.0, 0.0),
                               localAnchorB=(0.0, self.LINK_HEIGHT / 2),
                               enableMotor=True, maxMotorTorque=self.MAX_JOINT_TORQUE, motorSpeed=0.0)
        self.joints.append(self.world.CreateJoint(rjd))

    def _create_next_arm(self):
        self.joint_bodies.append(
            self.world.CreateDynamicBody(
                position=self.links[-1].position - (0.0, self.LINK_HEIGHT / 2), fixtures=self.FIX_CIRCLE))
        self.joint_bodies[-1].color1 = self.COLOR_JOINTS
        self.joint_bodies[-1].color2 = self.COLOR_BORDER

        rjd = weldJointDef(bodyA=self.links[-1], bodyB=self.joint_bodies[-1],
                           localAnchorA=(0.0, -self.LINK_HEIGHT / 2), localAnchorB=(0.0, 0.0))
        self.joint_fixes.append(self.world.CreateJoint(rjd))

        self.links.append(
            self.world.CreateDynamicBody(position=self.joint_bodies[-1].position - (0.0, self.LINK_HEIGHT / 2),
                                         angle=0.0, fixtures=self.FIX_POLY))
        self.links[-1].color1 = self.COLOR_LINKS
        self.links[-1].color2 = self.COLOR_BORDER
        # print(f"link1 mass:         {self.links[-1].massData.mass}")
        # print(f"link1 inertia:      {self.links[-1].massData.I}")
        # print(f"link1 local center: {self.links[-1].localCenter}")
        # print(f"link1 world center: {self.links[-1].worldCenter}")

        rjd = revoluteJointDef(bodyA=self.links[-2], bodyB=self.links[-1], localAnchorA=(0.0, -self.LINK_HEIGHT / 2),
                               localAnchorB=(0.0, self.LINK_HEIGHT / 2),
                               enableMotor=True, maxMotorTorque=self.MAX_JOINT_TORQUE, motorSpeed=0.0)
        self.joints.append(self.world.CreateJoint(rjd))

    def _create_end_effector(self):
        self.end_effector = self.world.CreateDynamicBody(position=self.links[-1].position - (0.0, self.LINK_HEIGHT / 2),
                                                         fixtures=self.FIX_CIRCLE)
        self.end_effector.color1 = self.COLOR_EE
        self.end_effector.color2 = self.COLOR_BORDER

        rjd = weldJointDef(bodyA=self.links[-1], bodyB=self.end_effector,
                           localAnchorA=(0.0, -self.LINK_HEIGHT / 2), localAnchorB=(0.0, 0.0))
        self.joint_fixes.append(self.world.CreateJoint(rjd))

    def _create_target(self):
        self.target = self.world.CreateStaticBody(position=(self._random_point()), fixtures=self.FIX_CIRCLE)
        self.target.color1 = self.COLOR_TARGET
        self.target.color2 = self.COLOR_BORDER

    def _random_point(self):

        len_arms = self.n_links * self.LINK_HEIGHT * 1.

        while True:
            x = 2 * len_arms * self.np_random.rand() - len_arms + self.anchor.position[0]
            y = 2 * len_arms * self.np_random.rand() - len_arms + self.anchor.position[1]

            pos_1 = np.array([x, y])
            pos_2 = np.array([self.anchor.position[0], self.anchor.position[1]])

            distance = np.linalg.norm(pos_1 - pos_2)

            if self.n_links == 1:
                if abs(distance - len_arms) < self.MIN_DISTANCE:
                    return x, y
            else:
                if len_arms * 0.95 > distance:
                    return x, y

    def _calc_power(self, action_arr):
        power = 0
        for action in action_arr:
            power += abs(action)

        return power

    def _get_distance(self):

        pos1 = np.array([self.end_effector.position[0], self.end_effector.position[1]])
        pos2 = np.array([self.target.position[0], self.target.position[1]])

        distance = np.linalg.norm(pos1 - pos2)

        return distance

    def _get_terminal(self):

        distance = self._get_distance()

        if distance < self.MIN_DISTANCE:
            return True
        else:
            return False

    def reset(self):

        self.time_step = 0

        self._destroy()

        self.set_episode_parameters()

        self._create_anchor()
        self._create_first_arm()
        for _ in range(self.n_links - 1):
            self._create_next_arm()
        self._create_end_effector()

        self._create_target()

        self.links[self.n_links - 1].ground_contact = False
        self.draw_list = [self.anchor] + self.links + self.joint_bodies + [self.end_effector] + [self.target]

        # It is needed to perform one world step to correctly initialize joint angles/velocities
        for idx in range(len(self.joints)):
            self.joints[idx].motorSpeed = 0.0
        self.world.Step(1.0 / FPS, 6 * 300, 2 * 300)
        self.world.ClearForces()

        return self._get_state()

    def seed(self, seed=None):
        self.np_random, seed = seeding.np_random(seed)
        return [seed]

    def _get_state(self):

        tx = self.target.position[0]
        ty = self.target.position[1]

        eex = self.end_effector.position[0]
        eey = self.end_effector.position[1]

        state = [tx, ty, eex, eey]

        for idx in range(self.n_links):
            state.append(self.joints[idx].angle % (np.pi * 2) - np.pi)
            state.append(self.joints[idx].speed)

        return np.array(state)

    def step(self, action):

        self.time_step += 1

        action = np.clip(action, -1., 1.)
        power = self._calc_power(action)

        speeds = self.MAX_JOINT_VEL * action

        for idx in range(len(self.joints)):
            self.joints[idx].motorSpeed = float(speeds[idx])

        self.world.Step(1.0 / FPS, 6 * 300, 2 * 300)
        self.world.ClearForces()

        state = self._get_state()

        done = self._get_terminal()

        if done:
            reward = 300
        else:
            reward = -self._get_distance()
            if self.POWER:
                reward -= power

        if self.time_step > self.MAX_TIME_STEP:
            done = True

        if DEBUG:
            print("dist", self._get_distance())
            print("power", power)
            print("reward", reward)
            print("state", state)
            time.sleep(.1)

        return state, reward, done, {}

    def render(self, mode='human'):
        if self.viewer is None:
            self.viewer = rendering.Viewer(self.VIEWPORT_W, self.VIEWPORT_H)

        dim = self.VIEWPORT_W / self.SCALE / 2
        self.viewer.set_bounds(-dim, dim, -dim, dim)

        self.viewer.draw_polygon([
            (-self.VIEWPORT_H, -self.VIEWPORT_H),
            (self.VIEWPORT_H, -self.VIEWPORT_H),
            (self.VIEWPORT_H, self.VIEWPORT_H),
            (-self.VIEWPORT_H, self.VIEWPORT_H),
        ], color=self.COLOR_BACKGROUND)

        for obj in self.draw_list:
            for f in obj.fixtures:
                trans = f.body.transform
                if type(f.shape) is circleShape:
                    t = rendering.Transform(translation=trans * f.shape.pos)
                    self.viewer.draw_circle(f.shape.radius, 30, color=obj.color1).add_attr(t)
                    self.viewer.draw_circle(f.shape.radius, 30, color=obj.color2, filled=False, linewidth=2).add_attr(t)
                else:
                    path = [trans * v for v in f.shape.vertices]
                    self.viewer.draw_polygon(path, color=obj.color1)
                    path.append(path[0])
                    self.viewer.draw_polyline(path, color=obj.color2, linewidth=2)

        return self.viewer.render(return_rgb_array=mode == 'rgb_array')

    def close(self):
        if self.viewer is not None:
            self.viewer.close()
            self.viewer = None

    def set_episode_parameters(self):

        self.COLOR_ANCHOR = (0., 0., 0.)
        self.COLOR_JOINTS = (0.6, 0.6, .8)
        self.COLOR_BORDER = (0., 0., 0.)
        self.COLOR_LINKS = (.6, .6, 1.)
        self.COLOR_EE = (.6, 1., .6)
        self.COLOR_TARGET = (1., 0.6, 0.6)
        self.COLOR_BACKGROUND = (0.9, 0.9, 1.0)
        # self.update_visuals()

        self.MAX_JOINT_TORQUE = 10000  # 80

        self.ANCHOR_X = 0.0
        self.ANCHOR_Y = 0.0


if __name__ == '__main__':
    """
    Test fct for debugging purposes
    """
    num_links = 10
    nlinks = NLinksBox2DEnv(n_links=num_links)

    from stable_baselines.common.env_checker import check_env
    check_env(nlinks)

    # some silent initialization steps
    # for _ in range(100):
    #     nlinks.render()
    #     zero_act = np.zeros(num_links)
    #     nlinks.step(action=zero_act)

    while True:
        nlinks.render()
        # print("anchor", nlinks.anchor.position)
        # print("end_eff", nlinks.end_effector.position)
        # rnd_act = np.array([0.3, -0.3])
        # rnd_act = np.array([np.random.rand() * 2 - 1, np.random.rand() * 2 - 1])
        rnd_act = []
        for i in range(num_links):
            rnd_act.append(np.random.rand() * 2 - 1)

        s, _, d, _ = nlinks.step(action=rnd_act)
        # print("tx: {0:2.1f}; ty: {1:2.1f}".format(s[0], s[1]))
        # print("eex: {0:2.1f}; eey: {1:2.1f}".format(s[2], s[3]))
        if d:
            nlinks.reset()

