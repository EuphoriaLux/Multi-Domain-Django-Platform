package lu.crush.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.util.Log;
import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import java.util.Map;

public class MyFirebaseMessagingService extends FirebaseMessagingService {
    private static final String TAG = "CrushFCMService";
    private static final String PREFS_NAME = "CrushPreferences";
    private static final String KEY_FCM_TOKEN = "fcm_token";
    private static final String KEY_TOKEN_SYNCED = "fcm_token_synced";
    // Bumped from the original "crush_notifications": that channel shipped in
    // build 3 at IMPORTANCE_DEFAULT, and a channel's importance is immutable once
    // created. A fresh id is the only way to give upgrading users the heads-up
    // (IMPORTANCE_HIGH) channel; the old one is deleted in ensureNotificationChannel.
    private static final String CHANNEL_ID = "crush_notifications_v2";
    private static final String LEGACY_CHANNEL_ID = "crush_notifications";

    @Override
    public void onNewToken(@NonNull String token) {
        super.onNewToken(token);
        Log.d(TAG, "Refreshed FCM registration token: " + token);

        // Store the token in SharedPreferences
        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        prefs.edit()
                .putString(KEY_FCM_TOKEN, token)
                .putBoolean(KEY_TOKEN_SYNCED, false)
                .apply();
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage remoteMessage) {
        super.onMessageReceived(remoteMessage);
        Log.d(TAG, "From: " + remoteMessage.getFrom());

        // Check if message contains data payload
        Map<String, String> data = remoteMessage.getData();
        if (data.size() > 0) {
            Log.d(TAG, "Message data payload: " + data);
        }

        // Check if message contains a notification payload
        if (remoteMessage.getNotification() != null) {
            String title = remoteMessage.getNotification().getTitle();
            String body = remoteMessage.getNotification().getBody();
            Log.d(TAG, "Message Notification Title: " + title);
            Log.d(TAG, "Message Notification Body: " + body);

            // Display a notification if the app is in the foreground
            sendNotification(title, body, data);
        }
    }

    private void sendNotification(String title, String messageBody, Map<String, String> data) {
        Intent intent = new Intent(this, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);

        // Pass payload URL data to MainActivity so it can navigate on click
        if (data != null && data.containsKey("url")) {
            intent.putExtra("target_url", data.get("url"));
        }

        // Unique per notification: a fixed id would make every push overwrite
        // the previous one, and a fixed PendingIntent requestCode would reuse
        // the first notification's target_url extra for all later ones.
        int notificationId = (int) (System.currentTimeMillis() & 0x7FFFFFFF);

        PendingIntent pendingIntent = PendingIntent.getActivity(
                this, notificationId, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        NotificationManager notificationManager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);

        // Idempotent with the channel created at app startup; keeps the
        // foreground path working even if onCreate hasn't run this process.
        ensureNotificationChannel(this);

        NotificationCompat.Builder notificationBuilder =
                new NotificationCompat.Builder(this, CHANNEL_ID)
                        .setSmallIcon(R.mipmap.ic_launcher) // Reuses existing launcher icon
                        .setContentTitle(title)
                        .setContentText(messageBody)
                        .setAutoCancel(true)
                        .setContentIntent(pendingIntent);

        notificationManager.notify(notificationId, notificationBuilder.build());
    }

    /**
     * Create (or update) the high-importance notification channel.
     *
     * <p>Background FCM "notification" messages are rendered by the system, which
     * bypasses {@link #onMessageReceived} and looks up the channel named by the
     * {@code com.google.firebase.messaging.default_notification_channel_id}
     * manifest meta-data. That channel must already exist, so this is also called
     * from {@code MainActivity.onCreate()} to guarantee heads-up delivery for
     * pushes received while the app is backgrounded.
     */
    static void ensureNotificationChannel(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager notificationManager =
                context.getSystemService(NotificationManager.class);
        if (notificationManager == null) {
            return;
        }
        // Remove the superseded IMPORTANCE_DEFAULT channel so upgrading users
        // land on the high-importance one below (no-op on fresh installs).
        notificationManager.deleteNotificationChannel(LEGACY_CHANNEL_ID);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Crush Notifications",
                NotificationManager.IMPORTANCE_HIGH
        );
        channel.setDescription("Crush push notification updates");
        notificationManager.createNotificationChannel(channel);
    }
}
