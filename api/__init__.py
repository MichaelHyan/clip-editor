# -*- coding: utf-8 -*-
"""REST 蓝图注册。"""
from typing import Any

from . import (routes_agent, routes_assets, routes_clip, routes_effects,
               routes_export, routes_history, routes_media, routes_project)


def register(app: Any) -> None:
    app.register_blueprint(routes_project.bp, url_prefix="/api/projects")
    app.register_blueprint(routes_clip.bp, url_prefix="/api/projects")
    app.register_blueprint(routes_history.bp, url_prefix="/api/projects")
    app.register_blueprint(routes_media.bp, url_prefix="/api/media")
    app.register_blueprint(routes_effects.bp, url_prefix="/api/effects")
    app.register_blueprint(routes_agent.bp, url_prefix="/api/agent")
    app.register_blueprint(routes_export.bp, url_prefix="/api/export")
    app.register_blueprint(routes_assets.bp, url_prefix="/api/projects")


# __EOF__