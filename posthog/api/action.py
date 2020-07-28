from django.db.models.expressions import Subquery
from posthog.models import (
    Event,
    Team,
    Action,
    ActionStep,
    DashboardItem,
    User,
    Person,
    Filter,
    Entity,
    Cohort,
    CohortPeople,
)
from posthog.utils import (
    append_data,
    get_compare_period_dates,
    TemporaryTokenAuthentication,
)
from posthog.constants import (
    TREND_FILTER_TYPE_ACTIONS,
    TREND_FILTER_TYPE_EVENTS,
    TRENDS_CUMULATIVE,
    TRENDS_STICKINESS,
)
from posthog.tasks.calculate_action import calculate_action
from rest_framework import request, serializers, viewsets, authentication
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import (
    Q,
    Count,
    Sum,
    Avg,
    Min,
    Max,
    Prefetch,
    functions,
    QuerySet,
    OuterRef,
    Exists,
    Value,
    BooleanField,
    FloatField,
)
from django.db.models.expressions import RawSQL
from django.db.models.functions import Cast
from django.db import connection
from django.utils.timezone import now
from typing import Any, List, Dict, Optional, Tuple, Union
from datetime import timedelta
import pandas as pd
import datetime
import json
import copy
import numpy as np
from dateutil.relativedelta import relativedelta
from .person import PersonSerializer
from posthog.decorators import cached_function, TRENDS_ENDPOINT
from posthog.queries import stickiness, trends, retention, base


class ActionStepSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = ActionStep
        fields = [
            "id",
            "event",
            "tag_name",
            "text",
            "href",
            "selector",
            "url",
            "name",
            "url_matching",
            "properties",
        ]


class ActionSerializer(serializers.HyperlinkedModelSerializer):
    steps = serializers.SerializerMethodField()
    count = serializers.SerializerMethodField()

    class Meta:
        model = Action
        fields = [
            "id",
            "name",
            "post_to_slack",
            "steps",
            "created_at",
            "deleted",
            "count",
            "is_calculating",
        ]

    def get_steps(self, action: Action):
        steps = action.steps.all()
        return ActionStepSerializer(steps, many=True).data

    def get_count(self, action: Action) -> Optional[int]:
        if hasattr(action, "count"):
            return action.count  # type: ignore
        return None


def get_actions(queryset: QuerySet, params: dict, team_id: int) -> QuerySet:
    if params.get("include_count"):
        queryset = queryset.annotate(count=Count(TREND_FILTER_TYPE_EVENTS))

    queryset = queryset.prefetch_related(Prefetch("steps", queryset=ActionStep.objects.order_by("id")))
    return queryset.filter(team_id=team_id).order_by("-id")


class ActionViewSet(viewsets.ModelViewSet):
    queryset = Action.objects.all()
    serializer_class = ActionSerializer
    authentication_classes = [
        TemporaryTokenAuthentication,
        authentication.SessionAuthentication,
        authentication.BasicAuthentication,
    ]

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "list":  # type: ignore
            queryset = queryset.filter(deleted=False)
        return get_actions(queryset, self.request.GET.dict(), self.request.user.team_set.get().pk)

    def create(self, request: request.Request, *args: Any, **kwargs: Any) -> Response:
        action, created = Action.objects.get_or_create(
            name=request.data["name"],
            team=request.user.team_set.get(),
            deleted=False,
            defaults={"post_to_slack": request.data.get("post_to_slack", False), "created_by": request.user,},
        )
        if not created:
            return Response(data={"detail": "action-exists", "id": action.pk}, status=400)

        if request.data.get("steps"):
            for step in request.data["steps"]:
                ActionStep.objects.create(
                    action=action, **{key: value for key, value in step.items() if key not in ("isNew", "selection")}
                )
        calculate_action.delay(action_id=action.pk)
        return Response(ActionSerializer(action, context={"request": request}).data)

    def update(self, request: request.Request, *args: Any, **kwargs: Any) -> Response:
        action = Action.objects.get(pk=kwargs["pk"], team=request.user.team_set.get())

        # If there's no steps property at all we just ignore it
        # If there is a step property but it's an empty array [], we'll delete all the steps
        if "steps" in request.data:
            steps = request.data.pop("steps")
            # remove steps not in the request
            step_ids = [step["id"] for step in steps if step.get("id")]
            action.steps.exclude(pk__in=step_ids).delete()

            for step in steps:
                if step.get("id"):
                    db_step = ActionStep.objects.get(pk=step["id"])
                    step_serializer = ActionStepSerializer(db_step)
                    step_serializer.update(db_step, step)
                else:
                    ActionStep.objects.create(
                        action=action,
                        **{key: value for key, value in step.items() if key not in ("isNew", "selection")}
                    )

        serializer = ActionSerializer(action, context={"request": request})
        serializer.update(action, request.data)
        action.is_calculating = True
        calculate_action.delay(action_id=action.pk)
        return Response(ActionSerializer(action, context={"request": request}).data)

    def list(self, request: request.Request, *args: Any, **kwargs: Any) -> Response:
        actions = self.get_queryset()
        actions_list: List[Dict[Any, Any]] = ActionSerializer(actions, many=True, context={"request": request}).data  # type: ignore
        if request.GET.get("include_count", False):
            actions_list.sort(key=lambda action: action.get("count", action["id"]), reverse=True)
        return Response({"results": actions_list})

    @action(methods=["GET"], detail=False)
    def trends(self, request: request.Request, *args: Any, **kwargs: Any) -> Response:
        result = self._calculate_trends(request)
        return Response(result)

    @cached_function(cache_type=TRENDS_ENDPOINT)
    def _calculate_trends(self, request: request.Request) -> List[Dict[str, Any]]:
        team = request.user.team_set.get()
        filter = Filter(request=request)
        if filter.shown_as == "Stickiness":
            result = stickiness.Stickiness().run(filter, team)
        else:
            result = trends.Trends().run(filter, team)

        dashboard_id = request.GET.get("from_dashboard", None)
        if dashboard_id:
            DashboardItem.objects.filter(pk=dashboard_id).update(last_refresh=datetime.datetime.now())

        return result

    @action(methods=["GET"], detail=False)
    def retention(self, request: request.Request, *args: Any, **kwargs: Any) -> Response:
        team = request.user.team_set.get()
        properties = request.GET.get("properties", "{}")
        filter = Filter(data={"properties": json.loads(properties)})
        filter._date_from = "-11d"
        result = retention.Retention().run(filter, team)
        return Response({"data": result})

    @action(methods=["GET"], detail=False)
    def people(self, request: request.Request, *args: Any, **kwargs: Any) -> Response:
        team = request.user.team_set.get()
        filter = Filter(request=request)
        offset = int(request.GET.get("offset", 0))

        def _calculate_people(events: QuerySet, offset: int):
            shown_as = request.GET.get("shown_as")
            if shown_as is not None and shown_as == "Stickiness":
                stickiness_days = int(request.GET["stickiness_days"])
                events = (
                    events.values("person_id")
                    .annotate(day_count=Count(functions.TruncDay("timestamp"), distinct=True))
                    .filter(day_count=stickiness_days)
                )
            else:
                events = events.values("person_id").distinct()

            if request.GET.get("breakdown_type") == "cohort" and request.GET.get("breakdown_value") != "all":
                events = events.filter(
                    Exists(
                        CohortPeople.objects.filter(
                            cohort_id=int(request.GET["breakdown_value"]), person_id=OuterRef("person_id"),
                        ).only("id")
                    )
                )
            if request.GET.get("breakdown_type") == "person":
                events = events.filter(
                    Exists(
                        Person.objects.filter(
                            **{
                                "id": OuterRef("person_id"),
                                "properties__{}".format(request.GET["breakdown"]): request.GET["breakdown_value"],
                            }
                        ).only("id")
                    )
                )

            people = Person.objects.filter(team=team, id__in=[p["person_id"] for p in events[offset : offset + 100]],)

            people = people.prefetch_related(Prefetch("persondistinctid_set", to_attr="distinct_ids_cache"))

            return serialize_people(people=people, request=request)

        filtered_events: QuerySet = QuerySet()
        if request.GET.get("session"):
            filtered_events = (
                Event.objects.filter(team=team).filter(base.filter_events(team.pk, filter)).add_person_id(team.pk)
            )
        else:
            if len(filter.entities) >= 1:
                entity = filter.entities[0]
            else:
                entity = Entity({"id": request.GET["entityId"], "type": request.GET["type"]})

            if entity.type == TREND_FILTER_TYPE_EVENTS:
                filtered_events = base.process_entity_for_events(entity, team_id=team.pk, order_by=None).filter(
                    base.filter_events(team.pk, filter, entity)
                )
            elif entity.type == TREND_FILTER_TYPE_ACTIONS:
                actions = super().get_queryset()
                actions = actions.filter(deleted=False)
                try:
                    action = actions.get(pk=entity.id)
                except Action.DoesNotExist:
                    return Response([])
                filtered_events = base.process_entity_for_events(entity, team_id=team.pk, order_by=None).filter(
                    base.filter_events(team.pk, filter, entity)
                )

        people = _calculate_people(events=filtered_events, offset=offset)

        current_url = request.get_full_path()
        next_url: Optional[str] = request.get_full_path()
        if people["count"] > 99 and next_url:
            if "offset" in next_url:
                next_url = next_url[1:]
                next_url = next_url.replace("offset=" + str(offset), "offset=" + str(offset + 100))
            else:
                next_url = request.build_absolute_uri(
                    "{}{}offset={}".format(next_url, "&" if "?" in next_url else "?", offset + 100)
                )
        else:
            next_url = None

        return Response({"results": [people], "next": next_url, "previous": current_url[1:]})


def serialize_people(people: QuerySet, request: request.Request) -> Dict:
    people_dict = [PersonSerializer(person, context={"request": request}).data for person in people]
    return {"people": people_dict, "count": len(people_dict)}
