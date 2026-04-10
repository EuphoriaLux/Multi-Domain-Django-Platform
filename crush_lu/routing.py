from django.urls import re_path

from crush_lu.consumers import CheckinConsumer, QuizConsumer

websocket_urlpatterns = [
    re_path(r"ws/quiz/(?P<quiz_id>\d+)/$", QuizConsumer.as_asgi()),
    re_path(r"ws/checkin/(?P<event_id>\d+)/$", CheckinConsumer.as_asgi()),
]
