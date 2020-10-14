from typing import List
from uuid import uuid4

from ee.clickhouse.models.action import query_action
from ee.clickhouse.models.event import create_event
from ee.clickhouse.util import ClickhouseTestMixin
from posthog.models.action import Action
from posthog.models.event import Event
from posthog.models.person import Person
from posthog.test.test_event_model import filter_by_actions_factory


def _create_event(**kwargs) -> Event:
    pk = uuid4()
    kwargs.update({"event_uuid": pk})
    create_event(**kwargs)
    return Event(pk=pk)


def _get_events_for_action(action: Action) -> List[Event]:
    bla = query_action(action)
    ret = []
    for event in bla:
        ev = Event(pk=event[0])
        ev.person_id = event[-1]
        ret.append(ev)
    return ret


def _create_person(**kwargs) -> Person:
    person = Person.objects.create(**kwargs)
    return Person(id=person.uuid)


class TestActions(
    ClickhouseTestMixin, filter_by_actions_factory(_create_event, _create_person, _get_events_for_action)
):
    pass
