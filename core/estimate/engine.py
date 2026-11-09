# 推定の結果をためておく。曲線・残差・類似度と曲ごとの推定は、データか計算の設定が変わった時だけ作り直す
# （条件の変更では組み立て＝assemble.py だけをやり直す。仕様書「条件を変えてからの再計算は1秒以内」）。
# 計算の設定はセットごとに違ってよいので、設定の値の組ごとにためておく（同じ値のセット同士は同じ結果を使う）
import threading, time
from collections import OrderedDict

from . import model as M, recommend as R

MODEL_KEYS = ('player_id', 'pool_above', 'pool_below', 'pool_max', 'min_scores', 'self_half_life_days', 'min_overlap', 'overlap_shrink',
              'top_similar')
EST_KEYS = ('min_similar_on_map', 'self_weight', 'similar_prior', 'fc_gap_factor', 'stall_play_count', 'stall_days',
            'beyond_star_factor', 'clear_factor', 'unplayed_pass_drop', 'unplayed_tech_drop', 'acc_cap_margin')
KEEP = 6   # ためておく組の数（セットの数より少し多めに。古い物から捨てる）


class Engine:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self._lock = threading.Lock()
        self._models = OrderedDict()   # mkey → model（None＝データが無い）
        self._ests = OrderedDict()     # ekey → 推定の並び
        self._dk = None

    def _data_key(self):
        return (self.store.meta('last_run_at'), self.store.meta('ranking_at'), self.store.meta('self_scores_synced_at'))

    @staticmethod
    def _put(cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > KEEP:
            cache.popitem(last=False)

    def get(self, settings=None):
        """(model, 推定の並び)。settings はセットから見た設定（Settings.view）。省略時は画面で開いているセット。データが無ければ (None, [])"""
        s = (settings or self.settings).values()
        dk = self._data_key()
        mkey = tuple(str(s[k]) for k in MODEL_KEYS)
        ekey = mkey + tuple(str(s[k]) for k in EST_KEYS)
        with self._lock:
            if dk != self._dk:   # データが変わったら全部作り直す
                self._models.clear()
                self._ests.clear()
                self._dk = dk
            if mkey not in self._models:
                model = M.build(self.store, settings or self.settings, int(time.time()))
                if model is not None:
                    M.save_similarities(self.store, model)
                self._put(self._models, mkey, model)
                for k in [k for k in self._ests if k[:len(mkey)] == mkey]:   # 前の model で求めた推定は捨てる
                    del self._ests[k]
            model = self._models[mkey]
            self._models.move_to_end(mkey)
            if model is None:
                return None, []
            if ekey not in self._ests:
                self._put(self._ests, ekey, R.estimate(self.store, model, settings or self.settings, model.built_at))
            est = self._ests[ekey]
            self._ests.move_to_end(ekey)
            return model, est
