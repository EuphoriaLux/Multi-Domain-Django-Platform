from django.urls import re_path

from crush_lu.consumers import CacheHuntConsumer, CheckinConsumer, QuizConsumer
from crush_lu.consumers_event_lobby import EventLobbyConsumer
from crush_lu.consumers_video_call import VideoCallConsumer

websocket_urlpatterns = [
    re_path(r"ws/quiz/(?P<quiz_id>\d+)/$", QuizConsumer.as_asgi()),
    re_path(r"ws/checkin/(?P<event_id>\d+)/$", CheckinConsumer.as_asgi()),
    re_path(r"ws/cache/(?P<hunt_id>\d+)/$", CacheHuntConsumer.as_asgi()),
    re_path(r"ws/event-lobby/(?P<event_id>\d+)/$", EventLobbyConsumer.as_asgi()),
    re_path(
        r"ws/video-call/(?P<kind>[a-z][a-z0-9-]{0,31})/(?P<object_id>[A-Za-z0-9]{1,32})/$",
        VideoCallConsumer.as_asgi(),
    ),
]
