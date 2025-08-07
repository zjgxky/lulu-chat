from django.urls import path
from .views import chatbot_view, session_list_view, new_session_view, dify_proxy, dify_streaming_proxy, process_streamed_response, delete_session_view, login_view, logout_view, dashboard_view, feedback_view, get_feedback_state, execute_python_script_view, add_to_faq_view, check_faq_status, settings_view

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("", dashboard_view, name="dashboard"),
    path("dashboard/", dashboard_view, name="dashboard"),
    path("settings/", settings_view, name="settings"),
    path("sessions/", session_list_view, name="session_list"),
    path("sessions/new/", new_session_view, name="new_session"),
    path("sessions/<int:session_id>/", chatbot_view, name="chat_session"),
    path("sessions/<int:session_id>/delete/", delete_session_view, name="delete_session"),
    path("api/dify_proxy/", dify_proxy, name="dify_proxy"),
    path("api/dify_streaming_proxy/", dify_streaming_proxy, name="dify_streaming_proxy"),
    path("api/process_streamed_response/", process_streamed_response, name="process_streamed_response"),
    path("api/feedback/", feedback_view, name="feedback"),
    path("api/feedback-state/<int:session_id>/", get_feedback_state, name="get_feedback_state"),
    path("api/execute-python/", execute_python_script_view, name="execute_python_script"),
    path("api/add-to-faq/", add_to_faq_view, name="add_to_faq"),
    path("api/check-faq-status/<int:session_id>/", check_faq_status, name="check_faq_status"),
]
