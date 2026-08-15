package app.nexus.mobile

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import app.nexus.mobile.network.HermesCronJob

@Composable
internal fun ManagementDialogs(state: MainUiState, viewModel: MainViewModel) {
    state.modelPicker?.let { kind ->
        ModelPickerDialog(kind, state, viewModel)
    }
    if (state.cronManagerOpen) {
        CronManagerDialog(state, viewModel)
    }
    state.cronEditor?.let { editor ->
        CronJobEditorDialog(editor, state.cronBusyJobId != null, state.error, viewModel)
    }
    state.cronJobToDelete?.let { job ->
        CronJobDeleteDialog(job, state.cronBusyJobId == job.id, state.error, viewModel)
    }
}

@Composable
private fun ModelPickerDialog(kind: ModelPickerKind, state: MainUiState, viewModel: MainViewModel) {
    val title = when (kind) {
        ModelPickerKind.PROFILE -> "人格（Hermes Profile）"
        ModelPickerKind.INFERENCE -> "调用模型"
        ModelPickerKind.REASONING -> "推理深度"
    }
    val description = when (kind) {
        ModelPickerKind.PROFILE -> "人格名称从原版 Hermes Profile API 动态读取；不同人格的会话、记忆、工具和定时任务相互独立。"
        ModelPickerKind.INFERENCE -> "这里只显示 Hermes 当前提供的实际调用模型；Agent/人格路由不会出现在这里。"
        ModelPickerKind.REASONING -> "新对话默认使用自动；Nexus 不发送推理深度，由 Hermes 和实际模型决定。"
    }
    AlertDialog(
        onDismissRequest = viewModel::closeModelPicker,
        title = { Text(title) },
        text = {
            Column(
                Modifier
                    .fillMaxWidth()
                    .heightIn(max = 480.dp)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    description,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 13.sp
                )
                val loading = when (kind) {
                    ModelPickerKind.PROFILE -> state.profilesLoading
                    ModelPickerKind.INFERENCE -> state.modelsLoading
                    ModelPickerKind.REASONING -> false
                }
                if (loading) {
                    Box(Modifier.fillMaxWidth().height(120.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(modifier = Modifier.size(28.dp), strokeWidth = 2.dp)
                    }
                } else {
                    when (kind) {
                        ModelPickerKind.PROFILE -> ProfileOptions(state, viewModel)
                        ModelPickerKind.INFERENCE -> InferenceModelOptions(state, viewModel)
                        ModelPickerKind.REASONING -> ReasoningOptions(state, viewModel)
                    }
                }
                state.error?.let {
                    Text(it, color = MaterialTheme.colorScheme.error, fontSize = 12.sp)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = viewModel::closeModelPicker) { Text("完成") }
        },
        dismissButton = when (kind) {
            ModelPickerKind.PROFILE -> {
                {
                    TextButton(onClick = { viewModel.refreshProfiles() }, enabled = !state.profilesLoading) {
                        Text("刷新人格")
                    }
                }
            }
            ModelPickerKind.INFERENCE -> {
                {
                    TextButton(onClick = { viewModel.refreshModels() }, enabled = !state.modelsLoading) {
                        Text("刷新模型")
                    }
                }
            }
            ModelPickerKind.REASONING -> null
        }
    )
}

@Composable
private fun ProfileOptions(state: MainUiState, viewModel: MainViewModel) {
    if (state.profiles.isEmpty()) {
        Text(
            "未获取到 Hermes 人格。请检查 Gateway 与原版 Hermes Profile API 的连接后重试。",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontSize = 12.sp
        )
    } else {
        state.profiles.forEach { profile ->
            val connection = profile.connectionLabel
            val subtitle = when {
                !profile.available -> "$connection · 人格名称未验证"
                profile.isDefault -> "默认连接：$connection"
                else -> "连接：$connection"
            }
            ModelOptionRow(
                selected = state.selectedProfileId == profile.id,
                title = profile.displayName,
                subtitle = subtitle,
                onClick = { viewModel.selectProfile(profile.id) }
            )
        }
    }
    state.profileNotice?.let { notice ->
        Text(
            notice,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontSize = 12.sp,
            lineHeight = 17.sp
        )
    }
}

@Composable
private fun InferenceModelOptions(state: MainUiState, viewModel: MainViewModel) {
    if (state.inferenceModels.isEmpty()) {
        Text(
            state.modelNotice ?: "Hermes 没有返回可选择的已认证模型。",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontSize = 12.sp
        )
    } else {
        state.inferenceModels
            .groupBy { model -> model.providerId to model.providerName }
            .forEach { (provider, models) ->
                Text(
                    provider.second.ifBlank { provider.first },
                    modifier = Modifier.padding(top = 6.dp, start = 4.dp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.SemiBold
                )
                models.forEach { model ->
                    ModelOptionRow(
                        selected = state.selectedInferenceModelId == model.id &&
                            state.selectedInferenceProviderId == model.providerId,
                        title = model.displayName,
                        subtitle = null,
                        onClick = { viewModel.selectInferenceModel(model.providerId, model.id) }
                    )
                }
            }
    }
}

@Composable
private fun ReasoningOptions(state: MainUiState, viewModel: MainViewModel) {
    ReasoningEffort.entries.forEach { effort ->
        ModelOptionRow(
            selected = state.selectedReasoningEffort == effort,
            title = effort.label,
            subtitle = if (effort == ReasoningEffort.DEFAULT) {
                "Nexus 新对话默认值；不发送 reasoning_effort，由 Hermes 决定"
            } else {
                null
            },
            onClick = { viewModel.selectReasoningEffort(effort) }
        )
    }
}

@Composable
private fun ModelOptionRow(
    selected: Boolean,
    title: String,
    subtitle: String?,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .background(
                if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
                RoundedCornerShape(6.dp)
            )
            .padding(horizontal = 6.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        RadioButton(selected = selected, onClick = onClick)
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            subtitle?.let {
                Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun CronManagerDialog(state: MainUiState, viewModel: MainViewModel) {
    Dialog(
        onDismissRequest = viewModel::closeCronManager,
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)
    ) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize().navigationBarsPadding()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(MaterialTheme.colorScheme.surface)
                        .statusBarsPadding()
                        .height(58.dp)
                        .padding(horizontal = 6.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    IconButton(onClick = viewModel::closeCronManager) {
                        Icon(Icons.Filled.Close, contentDescription = "关闭定时任务")
                    }
                    Column(Modifier.weight(1f)) {
                        Text("定时任务", fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
                        Text(
                            "共 ${state.cronJobs.size} 个任务",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontSize = 11.sp
                        )
                    }
                    IconButton(onClick = viewModel::refreshCronJobs, enabled = !state.cronJobsLoading) {
                        Icon(Icons.Filled.Refresh, contentDescription = "刷新定时任务")
                    }
                    IconButton(onClick = viewModel::createCronJob, enabled = state.cronBusyJobId == null) {
                        Icon(Icons.Filled.Add, contentDescription = "新建定时任务")
                    }
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outline)

                state.cronNotice?.let { notice ->
                    Text(
                        notice,
                        color = MaterialTheme.colorScheme.primary,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                        fontSize = 13.sp
                    )
                }
                state.error?.let { error ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .background(MaterialTheme.colorScheme.errorContainer)
                            .padding(start = 16.dp, end = 6.dp, top = 5.dp, bottom = 5.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            error,
                            color = MaterialTheme.colorScheme.onErrorContainer,
                            modifier = Modifier.weight(1f),
                            fontSize = 12.sp
                        )
                        IconButton(onClick = viewModel::clearError, modifier = Modifier.size(34.dp)) {
                            Icon(
                                Icons.Filled.Close,
                                contentDescription = "关闭错误提示",
                                tint = MaterialTheme.colorScheme.onErrorContainer
                            )
                        }
                    }
                }

                when {
                    state.cronJobsLoading && state.cronJobs.isEmpty() -> {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }
                    }
                    state.cronJobs.isEmpty() -> {
                        Column(
                            Modifier.fillMaxSize().padding(horizontal = 32.dp, vertical = 48.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center
                        ) {
                            Text("暂无定时任务", fontWeight = FontWeight.SemiBold, fontSize = 17.sp)
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "创建任务后会显示在这里",
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                fontSize = 13.sp
                            )
                            Spacer(Modifier.height(16.dp))
                            Button(onClick = viewModel::createCronJob) {
                                Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(18.dp))
                                Spacer(Modifier.size(7.dp))
                                Text("新建任务")
                            }
                        }
                    }
                    else -> {
                        LazyColumn(
                            modifier = Modifier.fillMaxSize(),
                            contentPadding = PaddingValues(12.dp),
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            if (state.cronJobsLoading) {
                                item("loading") {
                                    Row(
                                        Modifier.fillMaxWidth().padding(8.dp),
                                        horizontalArrangement = Arrangement.Center
                                    ) {
                                        CircularProgressIndicator(modifier = Modifier.size(22.dp), strokeWidth = 2.dp)
                                    }
                                }
                            }
                            items(state.cronJobs, key = { it.id }) { job ->
                                CronJobCard(
                                    job = job,
                                    busy = state.cronBusyJobId == job.id,
                                    actionsEnabled = state.cronBusyJobId == null,
                                    onEdit = { viewModel.editCronJob(job) },
                                    onRun = { viewModel.runCronJobNow(job) },
                                    onToggle = { viewModel.toggleCronJob(job) },
                                    onDelete = { viewModel.requestDeleteCronJob(job) }
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun CronJobCard(
    job: HermesCronJob,
    busy: Boolean,
    actionsEnabled: Boolean,
    onEdit: () -> Unit,
    onRun: () -> Unit,
    onToggle: () -> Unit,
    onDelete: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 0.dp)
    ) {
        Column(Modifier.fillMaxWidth().padding(13.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    job.name,
                    modifier = Modifier.weight(1f),
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 15.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
                Spacer(Modifier.size(8.dp))
                CronStateBadge(job)
            }
            Text(job.schedule.displayValue, color = MaterialTheme.colorScheme.primary, fontSize = 12.sp, fontWeight = FontWeight.Medium)
            Text(
                job.prompt.ifBlank { "未填写任务内容" },
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 3,
                fontSize = 13.sp
            )
            Text(
                if (job.repeatTimes == null) "已执行 ${job.completedRuns} 次 · 持续执行"
                else "已执行 ${job.completedRuns} / ${job.repeatTimes} 次",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 12.sp
            )
            job.nextRunAt?.let {
                Text("下次执行：${friendlyCronTime(it)}", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
            }
            job.lastRunAt?.let {
                Text("上次执行：${friendlyCronTime(it)}", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
            }
            job.lastError?.takeIf { it.isNotBlank() }?.let {
                Text("最近错误：$it", color = MaterialTheme.colorScheme.error, fontSize = 12.sp, maxLines = 3)
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                IconButton(onClick = onEdit, enabled = actionsEnabled, modifier = Modifier.size(40.dp)) {
                    Icon(Icons.Filled.Edit, contentDescription = "编辑任务", modifier = Modifier.size(19.dp))
                }
                IconButton(onClick = onRun, enabled = actionsEnabled, modifier = Modifier.size(40.dp)) {
                    Icon(Icons.Filled.PlayArrow, contentDescription = "立即运行", modifier = Modifier.size(21.dp))
                }
                IconButton(onClick = onToggle, enabled = actionsEnabled, modifier = Modifier.size(40.dp)) {
                    Icon(
                        if (job.isPaused) Icons.Filled.PlayArrow else Icons.Filled.Pause,
                        contentDescription = if (job.isPaused) "恢复任务" else "暂停任务",
                        modifier = Modifier.size(20.dp)
                    )
                }
                IconButton(onClick = onDelete, enabled = actionsEnabled, modifier = Modifier.size(40.dp)) {
                    Icon(
                        Icons.Filled.Delete,
                        contentDescription = "删除任务",
                        tint = if (actionsEnabled) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(19.dp)
                    )
                }
            }
            if (busy) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                }
            }
        }
    }
}

@Composable
private fun CronStateBadge(job: HermesCronJob) {
    val label = when {
        job.isPaused -> "已暂停"
        job.state.equals("running", ignoreCase = true) -> "运行中"
        else -> "已启用"
    }
    val background = when {
        job.isPaused -> MaterialTheme.colorScheme.surfaceVariant
        job.state.equals("running", ignoreCase = true) -> MaterialTheme.colorScheme.tertiaryContainer
        else -> MaterialTheme.colorScheme.primaryContainer
    }
    Surface(color = background, shape = RoundedCornerShape(999.dp)) {
        Text(label, modifier = Modifier.padding(horizontal = 9.dp, vertical = 4.dp), fontSize = 11.sp)
    }
}

@Composable
private fun CronJobEditorDialog(
    editor: CronJobEditorState,
    busy: Boolean,
    error: String?,
    viewModel: MainViewModel
) {
    val repeatInvalid = !isValidRepeatInput(editor.repeatText)
    AlertDialog(
        onDismissRequest = viewModel::cancelCronEditor,
        title = { Text(if (editor.jobId == null) "新建定时任务" else "编辑定时任务") },
        text = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(max = 560.dp)
                    .verticalScroll(rememberScrollState())
                    .imePadding(),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                error?.let { DialogErrorMessage(it) }
                OutlinedTextField(
                    value = editor.name,
                    onValueChange = viewModel::updateCronJobName,
                    label = { Text("任务名称") },
                    singleLine = true,
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = editor.schedule,
                    onValueChange = viewModel::updateCronJobSchedule,
                    label = { Text("执行计划") },
                    placeholder = { Text("例如：0 9 * * *") },
                    singleLine = true,
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth()
                )
                Text(
                    "支持 Cron（0 9 * * *）、固定间隔（every 2h）或 ISO 单次时间。Cron 按 Hermes 所在电脑的时区执行。",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontSize = 11.sp
                )
                OutlinedTextField(
                    value = editor.prompt,
                    onValueChange = viewModel::updateCronJobPrompt,
                    label = { Text("任务内容 / Prompt") },
                    minLines = 4,
                    enabled = !busy,
                    modifier = Modifier.fillMaxWidth()
                )
                OutlinedTextField(
                    value = editor.repeatText,
                    onValueChange = viewModel::updateCronJobRepeat,
                    label = { Text("重复次数（可选）") },
                    placeholder = { Text("留空表示持续执行") },
                    supportingText = {
                        Text(if (repeatInvalid) "请输入大于 0 的整数" else "一次性任务可填写 1")
                    },
                    isError = repeatInvalid,
                    singleLine = true,
                    enabled = !busy,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    modifier = Modifier.fillMaxWidth()
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("启用任务")
                        Text(
                            if (editor.enabled) "保存后会按计划执行" else "保存后保持暂停",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontSize = 11.sp
                        )
                    }
                    Switch(
                        checked = editor.enabled,
                        onCheckedChange = viewModel::updateCronJobEnabled,
                        enabled = !busy
                    )
                }
            }
        },
        confirmButton = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (busy) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.size(6.dp))
                }
                TextButton(onClick = viewModel::saveCronJob, enabled = !busy && !repeatInvalid) { Text("保存") }
            }
        },
        dismissButton = { TextButton(onClick = viewModel::cancelCronEditor, enabled = !busy) { Text("取消") } }
    )
}

@Composable
private fun CronJobDeleteDialog(
    job: HermesCronJob,
    busy: Boolean,
    error: String?,
    viewModel: MainViewModel
) {
    AlertDialog(
        onDismissRequest = viewModel::cancelDeleteCronJob,
        title = { Text("删除定时任务？") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("将永久删除“${job.name}”，此操作不能撤销。已经产生的执行记录不会作为普通对话显示。")
                error?.let { DialogErrorMessage(it) }
            }
        },
        confirmButton = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (busy) {
                    CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.size(6.dp))
                }
                TextButton(onClick = viewModel::confirmDeleteCronJob, enabled = !busy) {
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
            }
        },
        dismissButton = { TextButton(onClick = viewModel::cancelDeleteCronJob, enabled = !busy) { Text("取消") } }
    )
}

@Composable
private fun DialogErrorMessage(error: String) {
    Text(
        text = error,
        color = MaterialTheme.colorScheme.onErrorContainer,
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.errorContainer, RoundedCornerShape(8.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        fontSize = 12.sp
    )
}

private fun friendlyCronTime(value: String): String =
    value.replace('T', ' ').removeSuffix("Z")
