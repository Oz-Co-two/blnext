# 全曲データ（core/fetch/song_details.py）: 読み取り・取得・違いの求め方。ネットに出ない
import gzip, io, urllib.error

import pytest

from core.fetch import song_details as sd
from core.fetch.http import Cancelled
from fakebl import Stream, song_details_gz

H1, H2, H3 = 'aa' * 20, 'bb' * 20, 'cc' * 20
SONGS = [(H1, 2, [(1, 4, 612), (2, 3, 500), (1, 2, 0)]),   # BeatLeader のランク。★0 の難易度は入れない
         (H2, 1, [(1, 4, 700)]),                           # ScoreSaber だけのランク
         (H3, 2 | 8, [(9, 4, 800), (1, 9, 800)])]          # 知らないモード・難易度は入れない


def test_parse_only_beatleader_ranked():
    scrape, ranked = sd.parse(song_details_gz(SONGS, 1234))
    assert scrape == 1234
    assert ranked == {(H1, 'Standard', 'ExpertPlus'): 6.12, (H1, 'OneSaber', 'Expert'): 5.0}


@pytest.mark.parametrize('gz', [b'', b'not gzip', gzip.compress(b'\x08'), gzip.compress(b'\xff' * 20),
                                song_details_gz(SONGS, 1, version=4), song_details_gz([], 1)])
def test_parse_broken_is_unavailable(gz):
    with pytest.raises(sd.Unavailable):
        sd.parse(gz)


def test_parse_too_big(monkeypatch):
    monkeypatch.setattr(sd, 'MAX_UNZIP', 10)
    with pytest.raises(sd.Unavailable):
        sd.parse(song_details_gz(SONGS, 1))


def test_changes_and_signature():
    k = lambda h: (h, 'Standard', 'ExpertPlus')
    row = lambda h, stars: {'hash': h.upper(), 'mode': 'Standard', 'difficulty': 'ExpertPlus', 'stars': stars}
    ranked = {k(H1): 6.12, k(H2): 7.0}
    assert sd.changes(ranked, [row(H1, 6.125), row(H2, 7.0)]) == []      # 全曲データは 0.01 刻みなので小さな差は同じ
    assert sd.changes(ranked, [row(H1, 6.2), row(H2, 7.0)]) == [k(H1)]   # ★が違う
    assert sd.changes(ranked, [row(H1, 6.12)]) == [k(H2)]                # 全曲データにだけある
    assert sd.changes(ranked, [row(H1, 6.12), row(H2, 7.0), row(H3, 1.0)]) == [k(H3)]   # 手元にだけある
    assert sd.signature([k(H1)]) == sd.signature([k(H1)]) != sd.signature([k(H2)])


def opener(resp=None, exc=None, seen=None):
    def op(req, timeout):
        if seen is not None:
            seen.append(req)
        if exc:
            raise exc
        return resp
    return op


def test_fetch_ok_and_url_is_fixed():
    seen = []
    gz, etag = sd.fetch(opener(Stream(b'data', {'ETag': 'e1'}), seen=seen), 'ua')
    assert (gz, etag) == (b'data', 'e1')
    assert seen[0].full_url == sd.URL and seen[0].get_header('Accept-encoding') == 'identity' and not seen[0].get_header('If-none-match')


def test_fetch_not_modified():
    seen = []
    err = urllib.error.HTTPError('u', 304, 'nm', {}, io.BytesIO(b''))
    assert sd.fetch(opener(exc=err, seen=seen), 'ua', 'e1') == (None, 'e1')
    assert seen[0].get_header('If-none-match') == 'e1'


@pytest.mark.parametrize('exc', [urllib.error.HTTPError('u', 404, 'nf', {}, io.BytesIO(b'')), urllib.error.URLError('down'), OSError('x')])
def test_fetch_failure_is_unavailable(exc):
    with pytest.raises(sd.Unavailable):
        sd.fetch(opener(exc=exc), 'ua')


def test_fetch_empty_and_too_big(monkeypatch):
    with pytest.raises(sd.Unavailable):
        sd.fetch(opener(Stream(b'')), 'ua')
    monkeypatch.setattr(sd, 'MAX_DOWNLOAD', 2)
    with pytest.raises(sd.Unavailable):
        sd.fetch(opener(Stream(b'data')), 'ua')


def test_fetch_can_be_cancelled():
    with pytest.raises(Cancelled):
        sd.fetch(opener(Stream(b'data')), 'ua', cancelled=lambda: True)
