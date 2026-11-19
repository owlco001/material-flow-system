package com.company.logistics.data

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

/** Resumable, network-gated queue drain. Payload values are never logged. */
class OfflineSyncWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val repository = LogisticsRepository.get(applicationContext)
        val report = repository.syncPending()
        val hasPending = repository.hasPendingOperations()
        return if (shouldRetryForPendingQueue(report, hasPending)) Result.retry() else Result.success()
    }

    companion object {
        /** Retained for compatibility; per-record automatic retries are bounded in the repository. */
        const val MAX_RETRIES = 5
    }
}

/** Retry for pending work even when this invocation only observes a future backoff or a conflict. */
internal fun shouldRetryForPendingQueue(report: SyncReport, hasPending: Boolean): Boolean =
    hasPending || report.retrying > 0
