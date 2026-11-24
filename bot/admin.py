from django.contrib import admin

from .models import InboxEvent, OutboxReply, Reminder


@admin.register(InboxEvent)
class InboxEventAdmin(admin.ModelAdmin):
    list_display = ("comment_id", "source", "status", "attempts", "received_at", "processed_at")
    list_filter = ("status", "source")
    search_fields = ("comment_id", "media_id")
    readonly_fields = ("payload", "received_at", "processed_at")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("comment_id", "requester_username", "due_at", "status", "locale", "note", "created_at")
    list_filter = ("status", "locale", "source")
    search_fields = ("comment_id", "media_id", "requester_username", "comment_text")
    date_hierarchy = "due_at"


@admin.register(OutboxReply)
class OutboxReplyAdmin(admin.ModelAdmin):
    list_display = ("kind", "target_comment_id", "status", "send_after", "attempts", "sent_at")
    list_filter = ("status", "kind", "source")
    search_fields = ("target_comment_id", "media_id", "message")
