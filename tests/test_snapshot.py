import pytest
import orion_results as api


def test_snapshot_budget_counts_every_emitted_byte(tmp_path):
    with api.Session.create(tmp_path/'records') as s:
        for n in range(40):
            s.capture(str(n),'synthetic-'+str(n))
        for budget in (256,257,509,1025,32000):
            view=s.snapshot(budget_bytes=budget)
            assert view.utf8_bytes==len(view.text.encode('utf-8'))<=budget
            assert view.shown_unique+view.omitted_unique==view.total_unique==40
            if view.omitted_unique:
                assert f'omitted={view.omitted_unique}' in view.text
                assert f'total={view.total_unique}' in view.text
                assert s.session_id in view.text
        assert s.snapshot(budget_bytes=256)==s.snapshot(budget_bytes=256)


def test_repeats_deduplicate_without_reordering_or_extra_records(tmp_path):
    root=tmp_path/'records'
    with api.Session.create(root,limits=api.Limits(max_results=2,max_payload_bytes=9)) as s:
        a=s.capture('first','日本').reference
        b=s.capture('second','abc').reference
        before=s.snapshot(include_content=True)
        repeated=s.capture('different-key','日本')
        assert repeated.deduplicated and repeated.reference==a
        assert s.snapshot(include_content=True)==before
        assert before.text.index(str(b)) < before.text.index(str(a))
        assert 'first' not in before.text
        sid=s.session_id
    assert api.inspect_session(root,sid).references==(a,b)


def test_same_key_changed_text_is_distinct(tmp_path):
    with api.Session.create(tmp_path/'records') as s:
        a=s.capture('same-key','old')
        b=s.capture('same-key','new')
        assert a.reference!=b.reference and not b.deduplicated
        view=s.snapshot()
        assert view.total_unique==2
        assert view.text.index(str(b.reference)) < view.text.index(str(a.reference))


def test_minimum_budget_unicode_and_reference_flood(tmp_path):
    root=tmp_path/'records'
    values=['😀日本\x00\n'+str(n) for n in range(80)]
    with api.Session.create(root) as s:
        assert s.snapshot().text==''
        refs=[s.capture(str(n),v).reference for n,v in enumerate(values)]
        for budget in (256,257,299,701,10007):
            v=s.snapshot(budget_bytes=budget,include_content=True)
            assert len(v.text.encode())==v.utf8_bytes<=budget
            assert v.omitted_unique==80-v.shown_unique
            for line in v.text.splitlines():
                if line.startswith('r1:'):
                    assert api.Reference.parse(line.split(' content=')[0]) in refs
        for invalid in (True,False,255,16_777_217,256.0,'256'):
            with pytest.raises(api.ResultError):
                s.snapshot(budget_bytes=invalid)
        sid=s.session_id
        assert not any(isinstance(v,(list,dict)) for v in vars(s).values())
    report=api.inspect_session(root,sid)
    assert report.exhaustive and report.references==tuple(refs)
    assert [api.read(root,r) for r in report.references]==values


@pytest.mark.parametrize('limits,initial,rejected',[
    (api.Limits(max_input_bytes=6),['日本'],'日本a'),
    (api.Limits(max_payload_bytes=9),['日本','abc'],'z'),
    (api.Limits(max_results=2),['one','two'],'three')])
def test_exact_record_count_and_payload_quota_refusal_preserve_prior_state(tmp_path,limits,initial,rejected):
    root=tmp_path/'records'
    with api.Session.create(root,limits=limits) as s:
        refs=[s.capture('key',text).reference for text in initial]
        before=s.snapshot(include_content=True)
        with pytest.raises(api.ResultError) as failed:
            s.capture('REJECTED-KEY',rejected)
        assert failed.value.code=='limit_exceeded'
        assert failed.value.outcome=='not_stored'
        assert rejected not in str(failed.value)
        assert s.snapshot(include_content=True)==before
        assert s.capture('duplicate',initial[0]).deduplicated
        assert [api.read(root,r) for r in refs]==initial


def test_collision_is_not_dedup_or_overwrite(tmp_path,monkeypatch):
    # Inject a digest identity to simulate collision, never claim a native SHA collision.
    root=tmp_path/'records'
    with api.Session.create(root) as s:
        first=s.capture('original','old').reference
        with monkeypatch.context() as m:
            m.setattr(api.Reference,'from_text',classmethod(lambda cls,sid,text: first))
            with pytest.raises(api.ResultError) as failed:
                s.capture('new','new')
        assert failed.value.code=='content_conflict'
        assert failed.value.outcome=='not_stored'
        assert api.read(root,first)=='old'
        assert s.snapshot().total_unique==1
