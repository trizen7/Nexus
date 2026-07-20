package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class NotificationHelperTest {

    @Test
    fun `answer notification ids are stable and do not collide for known hash collisions`() {
        val first = NotificationHelper.answerNotificationId("FB")
        val second = NotificationHelper.answerNotificationId("Ea")

        assertEquals(first, NotificationHelper.answerNotificationId("FB"))
        assertNotEquals(first, second)
    }

    @Test
    fun `transfer notification ids are stable and do not collide`() {
        val first = NotificationHelper.transferNotificationId("session", "FB")
        val second = NotificationHelper.transferNotificationId("session", "Ea")

        assertEquals(first, NotificationHelper.transferNotificationId("session", "FB"))
        assertNotEquals(first, second)
    }

    @Test
    fun `transfer notification ids include session and file identity`() {
        assertNotEquals(
            NotificationHelper.transferNotificationId("session-a", "file"),
            NotificationHelper.transferNotificationId("session-b", "file")
        )
        assertNotEquals(
            NotificationHelper.transferNotificationId("session-a", "file-a"),
            NotificationHelper.transferNotificationId("session-a", "file-b")
        )
    }

    @Test
    fun `answer and transfer ids live in disjoint ranges`() {
        val answer = NotificationHelper.answerNotificationId("session-x")
        val foreground = NotificationHelper.answerForegroundNotificationId("session-x")
        val transfer = NotificationHelper.transferNotificationId("session-x", "file-y")

        assertNotEquals(answer, transfer)
        assertNotEquals(foreground, transfer)
        assertNotEquals(answer, foreground)
    }

    @Test
    fun `pending intent request codes are distinct per type and key`() {
        val answerCode = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.ANSWER,
            sessionId = "session-abc",
            fileKey = null
        )
        val transferCode = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.TRANSFER,
            sessionId = "session-abc",
            fileKey = "file-xyz"
        )

        assertNotEquals(answerCode, transferCode)
        assertEquals(
            answerCode,
            NotificationHelper.pendingIntentRequestCode(
                type = NotificationHelper.PendingIntentType.ANSWER,
                sessionId = "session-abc",
                fileKey = null
            )
        )
    }

    @Test
    fun `same type and same keys produce identical pending intent codes`() {
        val a = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.TRANSFER,
            sessionId = "s1",
            fileKey = "f1"
        )
        val b = NotificationHelper.pendingIntentRequestCode(
            type = NotificationHelper.PendingIntentType.TRANSFER,
            sessionId = "s1",
            fileKey = "f1"
        )
        assertEquals(a, b)
    }

    @Test
    fun `pending intent codes resist known Java hash collisions`() {
        assertNotEquals(
            NotificationHelper.pendingIntentRequestCode(NotificationHelper.PendingIntentType.TRANSFER, "session", "FB"),
            NotificationHelper.pendingIntentRequestCode(NotificationHelper.PendingIntentType.TRANSFER, "session", "Ea")
        )
    }

    @Test
    fun `pending intent data uniquely includes type session and file`() {
        val first = NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.TRANSFER, "FB", "file")
        val second = NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.TRANSFER, "Ea", "file")
        val answer = NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.ANSWER, "FB", "file")

        assertNotEquals(first, second)
        assertNotEquals(first, answer)
        assertEquals(first, NotificationHelper.pendingIntentData(NotificationHelper.PendingIntentType.TRANSFER, "FB", "file"))
    }
}
