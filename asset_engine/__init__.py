"""Pick one stock video or photo per scene, then hand it to the existing renderer.

Stock is searched and scored first. An AI picture is requested only for a scene
that stays under the score threshold. This package does not render video.
"""
from .select import choose_all


def choose(scenes, s, work, log=print):
    from .query_t5 import script_queries
    from .search import search_mixkit
    return choose_all(scenes, s, work, log, analyze=script_queries, search_mixkit=search_mixkit)
