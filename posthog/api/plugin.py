import json
from typing import Any, Dict

import requests
from django.conf import settings
from rest_framework import request, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from posthog.models import Plugin, PluginConfig
from posthog.plugins import Plugins, download_plugin_github_zip


class PluginSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plugin
        fields = ["id", "name", "description", "url", "config_schema", "tag", "from_json"]

    def create(self, validated_data: Dict, *args: Any, **kwargs: Any) -> Plugin:
        if not settings.INSTALL_PLUGINS_FROM_WEB:
            raise APIException("Plugin installation via the web is disabled!")
        if len(Plugin.objects.filter(name=validated_data["name"])) > 0:
            raise APIException('Plugin with name "{}" already installed!'.format(validated_data["name"]))
        validated_data["archive"] = download_plugin_github_zip(validated_data["url"], validated_data["tag"])
        if "from_json" in validated_data:  # prevent hackery
            del validated_data["from_json"]
        plugin = Plugin.objects.create(from_web=True, **validated_data)
        Plugins().publish_reload_command()
        return plugin

    def update(self, plugin: Plugin, validated_data: Dict, *args: Any, **kwargs: Any) -> Plugin:  # type: ignore
        if not settings.INSTALL_PLUGINS_FROM_WEB:
            raise APIException("Plugin installation via the web is disabled!")
        if plugin.from_json:
            raise APIException('Can not update plugin "{}", which is configured from posthog.json!'.format(plugin.name))
        plugin.name = validated_data.get("name", plugin.name)
        plugin.description = validated_data.get("description", plugin.description)
        plugin.url = validated_data.get("url", plugin.url)
        plugin.config_schema = validated_data.get("config_schema", plugin.config_schema)
        plugin.tag = validated_data.get("tag", plugin.tag)
        plugin.archive = download_plugin_github_zip(plugin.url, plugin.tag)
        plugin.save()
        Plugins().publish_reload_command()
        return plugin


class PluginViewSet(viewsets.ModelViewSet):
    queryset = Plugin.objects.all()
    serializer_class = PluginSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if not settings.INSTALL_PLUGINS_FROM_WEB:
            return queryset.none()
        return queryset

    @action(methods=["GET"], detail=False)
    def repository(self, request: request.Request):
        if not settings.INSTALL_PLUGINS_FROM_WEB:
            return Response([])
        url = "https://raw.githubusercontent.com/PostHog/plugins/main/plugins.json"
        plugins = requests.get(url)
        return Response(json.loads(plugins.text))

    def destroy(self, request: request.Request, pk=None) -> Response:  # type: ignore
        if not settings.INSTALL_PLUGINS_FROM_WEB:
            raise APIException("Plugin installation via the web is disabled!")
        plugin = Plugin.objects.get(pk=pk)
        if plugin.from_json:
            raise APIException('Can not delete plugin "{}", which is configured from posthog.json!'.format(plugin.name))
        plugin.delete()
        Plugins().publish_reload_command()
        return Response(status=204)


class PluginConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PluginConfig
        fields = ["id", "plugin", "enabled", "order", "config"]

    def create(self, validated_data: Dict, *args: Any, **kwargs: Any) -> PluginConfig:
        if not settings.CONFIGURE_PLUGINS_FROM_WEB:
            raise APIException("Plugin configuration via the web is disabled!")
        request = self.context["request"]
        plugin_config = PluginConfig.objects.create(team=request.user.team, **validated_data)
        Plugins().publish_reload_command(plugin_config.team.pk)
        return plugin_config

    def update(self, plugin_config: PluginConfig, validated_data: Dict, *args: Any, **kwargs: Any) -> PluginConfig:  # type: ignore
        if not settings.CONFIGURE_PLUGINS_FROM_WEB:
            raise APIException("Plugin configuration via the web is disabled!")
        plugin_config.enabled = validated_data.get("enabled", plugin_config.enabled)
        plugin_config.config = validated_data.get("config", plugin_config.config)
        plugin_config.order = validated_data.get("order", plugin_config.order)
        plugin_config.save()
        Plugins().publish_reload_command(plugin_config.team.pk)
        return plugin_config


class PluginConfigViewSet(viewsets.ModelViewSet):
    queryset = PluginConfig.objects.all()
    serializer_class = PluginConfigSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if not settings.CONFIGURE_PLUGINS_FROM_WEB:
            return queryset.none()
        return queryset.filter(team_id=self.request.user.team.pk)

    # we don't use this endpoint, but have something anyway to prevent team leakage
    def destroy(self, request: request.Request, pk=None) -> Response:  # type: ignore
        if not settings.CONFIGURE_PLUGINS_FROM_WEB:
            raise APIException("Plugin configuration via the web is disabled!")
        plugin_config = PluginConfig.objects.get(team=request.user.team, pk=pk)
        plugin_config.enabled = False
        plugin_config.save()
        return Response(status=204)
