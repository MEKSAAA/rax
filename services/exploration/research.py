"""Parallel exploration adapter; existing isolated research evaluation is retained."""
from services.controller.frontend import REGISTER_MULTI_ENV, ResearchFrontend
from services.benchmarks import ALL_ROBOT_TOOLS
from services.mcp_contract import BUNDLE_TOOLS
from .pool import ExplorationPool, LifecycleGate
from .transport import serve_socket


class ParallelResearchFrontend(ResearchFrontend):
    def __init__(self, supervisor, workspace, *, agent_container=None, pool_factory=ExplorationPool):
        super().__init__(supervisor, workspace, agent_container=agent_container)
        c = supervisor.config
        self.pool = pool_factory(c.root / 'exploration', count=c.exploration_envs, seeds=c.exploration_seeds,
            worker_config={'task': c.task, 'sim_gpu': c.sim_gpu, 'benchmark': c.benchmark,
                          'observation_profile': c.observation_profile, 'eval_seed': c.eval_seed,
                          'episode_seconds': c.formal.wall_seconds, 'published_root': str(supervisor.published_root)})
        supervisor.exploration_pool = self.pool
        self.gate = LifecycleGate()
        self.sync()

    def sync(self):
        with self.s._state_lock:
            status = self.pool.status()
            # The supervisor's budget counter: episodes charged, not refunded ones (pool.charged).
            self.s.state['exploration_started'] = status['exploration_episodes_charged']
            self.s.state['exploration_pool'] = status
            successes = [r for r in self.pool.state['episodes'] if r.get('task_complete')]
            if successes:
                self.s.state['interactive_success'] = {
                    k: successes[-1][k] for k in ('episode', 'env_id', 'episode_id') if k in successes[-1]}
            self.s._save()

    def tools(self):
        tools = self.contract.select(super().tools(), scoped=self.contract.scoped_tools)
        for tool in tools:
            if tool['name'] == 'register':
                tool['description'] = REGISTER_MULTI_ENV
            if tool['name'] in {'rehearse', 'submit'}:
                tool['description'] += ' Wait for outstanding robot calls in every environment first; this closes all live exploration environments. A rehearsal uses one episode from the shared budget.'
        return tools

    def status(self):
        return {**super().status(), 'exploration': self.pool.status()}

    def exploration_closed(self):
        return bool(self.s.state['formal_reserved']) or self.pool.status()['exploration_complete']

    def parallel_request(self, request):
        return (isinstance(request, dict) and request.get('method') == 'tools/call'
                and isinstance(request.get('params'), dict)
                and (request.get('params') or {}).get('name') in self.contract.scoped_tools)

    def call(self, name, args):
        if name in BUNDLE_TOOLS and not self.contract.bundle_tools:
            raise PermissionError('Unknown tool')  # Live-copy mode has no bundle evaluation.
        if name == 'status':
            if args:
                raise ValueError('status takes no arguments')
            return self.reply(self.status())
        if name in ALL_ROBOT_TOOLS:
            self.contract.require_robot(name, args)
        if name in self.contract.scoped_tools:
            if not isinstance(args, dict) or 'env_id' not in args:
                raise ValueError('Parallel exploration requires an explicit env_id')
            env_id, arguments = self.contract.route(name, args)
            if name in {'start_episode', 'finish', 'evaluate'} and arguments:
                raise ValueError('Unexpected lifecycle arguments')
            with self.gate.enter():
                if self.s.state['formal_reserved']:
                    raise RuntimeError('Exploration is closed to interactive control')
                try:
                    if name == 'start_episode':
                        return self.pool.start(env_id)
                    if name == 'finish':
                        return self.pool.finish(env_id)
                    return self.pool.call(env_id, name, arguments)
                finally:
                    self.sync()
        # Global calls run on the dispatcher main thread, preserving signal deadlines.
        with self.gate.enter(exclusive=name not in {'gemini_generate'} | self.contract.bench.unscoped_tools):
            if name in {'rehearse', 'submit'}:
                # Validate BEFORE abandoning exploration episodes.
                if not isinstance(args, dict) or set(args) != {'bundle'}:
                    raise ValueError('Expected bundle')
                manifest = self.s.state['bundles'][args['bundle']]
                if name == 'submit' and not self.s._has_qualifying_rehearsal(args['bundle'], manifest):
                    raise RuntimeError('Exact clean rehearsal required')
                if name == 'rehearse' and self.pool.status()['exploration_episodes_remaining'] == 0:
                    raise RuntimeError('Shared exploration episode budget exhausted')
                # A transient image/Docker/bundle failure must leave exploration open.
                self.s.preflight(args['bundle'], formal=name == 'submit')
                self.pool.finish_all()
                if name == 'submit':
                    self.pool.seal()
            try:
                return super().call(name, args)
            finally:
                self.sync()

    def serve_socket(self, listener):
        serve_socket(self, listener, slots=self.pool.count)

    def close(self):
        try:
            self.pool.close()
        finally:
            super().close()

    def interrupt(self):
        self.pool.interrupt()
