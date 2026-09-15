package com.company.logistics.data

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters

/** Resumable, network-gated queue drain. Payload values are never logged. */
class OfflineSyncWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
    override suspend fun doWork(): Result {
        val report = LogisticsRepository.get(applicationContext).syncPending()
        return when {
            report.conflict > 0 -> Result.success()
            report.failed > 0 && runAttemptCount < MAX_RETRIES -> Result.retry()
            else -> Result.success()
        }
    }
    companion object { const val MAX_RETRIES = 5 }
}
