from typing import Any, Dict

from django.db import transaction
from django.db.models import QuerySet, query
from django.shortcuts import get_object_or_404
from rest_framework import exceptions, mixins, response, serializers, status, viewsets
from rest_framework.serializers import raise_errors_on_nested_writes

from posthog.models import Organization, OrganizationMembership
from posthog.permissions import OrganizationAdminWritePermissions, OrganizationMemberPermissions


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
        ]

    def create(self, validated_data: Dict, *args: Any, **kwargs: Any) -> Organization:
        raise_errors_on_nested_writes("create", self, validated_data)
        request = self.context["request"]
        with transaction.atomic():
            organization = Organization.objects.create(**validated_data)
            OrganizationMembership.objects.create(
                organization=organization, user=request.user, level=OrganizationMembership.Level.ADMIN
            )
            request.user.current_organization = organization
            request.user.current_team = None
            request.user.save()
        return organization


class OrganizationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    pagination_class = None
    permission_classes = [OrganizationMemberPermissions, OrganizationAdminWritePermissions]
    queryset = Organization.objects.none()
    lookup_field = "id"
    ordering_fields = ["created_by"]
    ordering = ["-created_by"]

    def get_queryset(self) -> QuerySet:
        return Organization.objects.filter(
            id__in=OrganizationMembership.objects.filter(user=self.request.user).values("organization_id")
        )

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_value = self.kwargs[self.lookup_field]
        if lookup_value == "@current":
            return self.request.user.organization
        filter_kwargs = {self.lookup_field: lookup_value}
        obj = get_object_or_404(queryset, **filter_kwargs)
        self.check_object_permissions(self.request, obj)
        return obj
