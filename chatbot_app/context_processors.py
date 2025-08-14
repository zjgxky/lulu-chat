from .models import FAQ


def faq_sessions(request):
    """Context processor to provide FAQ sessions to all templates"""
    if request.user.is_authenticated or request.session.get('is_authenticated'):
        faq_sessions_queryset = FAQ.objects.all()
        faq_sessions = []
        for faq in faq_sessions_queryset:
            first_user_msg = faq.session.messages.filter(sender="user").order_by('timestamp').first()
            preview = first_user_msg.text if first_user_msg else None
            faq_sessions.append({
                'id': faq.id,  # FAQ ID for rename/delete operations
                'session_id': faq.session.id,  # Session ID for linking to chat
                'created_at': faq.session.created_at,
                'preview': preview,
                'faq_created_at': faq.created_at,
                'title': faq.session.title  # Add title for display
            })
        return {'faq_sessions': faq_sessions}
    return {'faq_sessions': []}
