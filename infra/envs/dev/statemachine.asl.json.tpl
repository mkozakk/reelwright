{
  "Comment": "montage-pipeline: Probe (Map) -> SessionValidate -> Analyze (Parallel of Maps) -> Plan -> Cut (Map) -> Render -> Finish",
  "StartAt": "RouteMode",
  "States": {
    "RouteMode": {
      "Type": "Choice",
      "Choices": [
        {
          "And": [
            { "Variable": "$.mode", "IsPresent": true },
            { "Variable": "$.mode", "StringEquals": "rerender" }
          ],
          "Next": "PrepareCut"
        }
      ],
      "Default": "ProbeMap"
    },
    "ProbeMap": {
      "Type": "Map",
      "ItemsPath": "$.source_items",
      "MaxConcurrency": 5,
      "ItemProcessor": {
        "ProcessorConfig": { "Mode": "INLINE" },
        "StartAt": "ProbeOne",
        "States": {
          "ProbeOne": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
              "FunctionName": "${probe_arn}",
              "Payload.$": "$"
            },
            "ResultSelector": { "src_id.$": "$.Payload.src_id" },
            "End": true
          }
        }
      },
      "ResultPath": "$.probe_results",
      "Next": "SessionValidate",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "SessionValidate": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${session_profile_arn}",
        "Payload.$": "$"
      },
      "ResultSelector": {
        "job_id.$": "$.Payload.job_id",
        "subtitles_enabled.$": "$.Payload.subtitles_enabled",
        "video_source_items.$": "$.Payload.video_source_items"
      },
      "ResultPath": "$",
      "Next": "AnalyzeParallel",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "AnalyzeParallel": {
      "Type": "Parallel",
      "ResultPath": "$.analyze",
      "Branches": [
        {
          "StartAt": "TranscribeChoice",
          "States": {
            "TranscribeChoice": {
              "Type": "Choice",
              "Choices": [
                {
                  "Variable": "$.subtitles_enabled",
                  "BooleanEquals": true,
                  "Next": "TranscribeMap"
                }
              ],
              "Default": "SkipTranscribe"
            },
            "TranscribeMap": {
              "Type": "Map",
              "ItemsPath": "$.video_source_items",
              "MaxConcurrency": 5,
              "ItemProcessor": {
                "ProcessorConfig": { "Mode": "INLINE" },
                "StartAt": "Transcribe",
                "States": {
                  "Transcribe": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::lambda:invoke",
                    "Parameters": {
                      "FunctionName": "${analyze_transcribe_arn}",
                      "Payload.$": "$"
                    },
                    "End": true
                  }
                }
              },
              "End": true
            },
            "SkipTranscribe": {
              "Type": "Pass",
              "Result": { "skipped": true },
              "End": true
            }
          }
        },
        {
          "StartAt": "LoudnessMap",
          "States": {
            "LoudnessMap": {
              "Type": "Map",
              "ItemsPath": "$.video_source_items",
              "MaxConcurrency": 5,
              "ItemProcessor": {
                "ProcessorConfig": { "Mode": "INLINE" },
                "StartAt": "Loudness",
                "States": {
                  "Loudness": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::lambda:invoke",
                    "Parameters": {
                      "FunctionName": "${analyze_loudness_arn}",
                      "Payload.$": "$"
                    },
                    "End": true
                  }
                }
              },
              "End": true
            }
          }
        },
        {
          "StartAt": "ScenesMap",
          "States": {
            "ScenesMap": {
              "Type": "Map",
              "ItemsPath": "$.video_source_items",
              "MaxConcurrency": 5,
              "ItemProcessor": {
                "ProcessorConfig": { "Mode": "INLINE" },
                "StartAt": "Scenes",
                "States": {
                  "Scenes": {
                    "Type": "Task",
                    "Resource": "arn:aws:states:::lambda:invoke",
                    "Parameters": {
                      "FunctionName": "${analyze_scenes_arn}",
                      "Payload.$": "$"
                    },
                    "End": true
                  }
                }
              },
              "End": true
            }
          }
        }
      ],
      "Next": "Plan",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "Plan": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${plan_arn}",
        "Payload.$": "$"
      },
      "ResultSelector": { "job_id.$": "$.Payload.job_id" },
      "ResultPath": "$",
      "Next": "PrepareCut",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "PrepareCut": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${cut_prepare_arn}",
        "Payload.$": "$"
      },
      "ResultSelector": { "job_id.$": "$.Payload.job_id", "batches.$": "$.Payload.batches" },
      "ResultPath": "$",
      "Next": "CutMap",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "CutMap": {
      "Type": "Map",
      "ItemsPath": "$.batches",
      "MaxConcurrency": 5,
      "ItemProcessor": {
        "ProcessorConfig": { "Mode": "INLINE" },
        "StartAt": "CutBatch",
        "States": {
          "CutBatch": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {
              "FunctionName": "${cut_arn}",
              "Payload.$": "$"
            },
            "ResultSelector": { "clips.$": "$.Payload.clips" },
            "End": true
          }
        }
      },
      "ResultPath": "$.cut_results",
      "Next": "AcquireRenderSlot",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "AcquireRenderSlot": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${semaphore_acquire_arn}",
        "Payload.$": "$"
      },
      "ResultSelector": { "job_id.$": "$.Payload.job_id" },
      "ResultPath": "$",
      "Retry": [
        {
          "ErrorEquals": ["SlotUnavailable"],
          "IntervalSeconds": 10,
          "MaxAttempts": 90,
          "BackoffRate": 1.0
        }
      ],
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }],
      "Next": "RenderSpot"
    },
    "RenderSpot": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": {
        "Cluster": "${ecs_cluster_arn}",
        "TaskDefinition": "${render_task_definition_arn}",
        "CapacityProviderStrategy": [{ "CapacityProvider": "FARGATE_SPOT", "Weight": 1 }],
        "NetworkConfiguration": {
          "AwsvpcConfiguration": {
            "Subnets": ${public_subnet_ids_json},
            "SecurityGroups": ["${render_task_sg_id}"],
            "AssignPublicIp": "ENABLED"
          }
        },
        "Overrides": {
          "ContainerOverrides": [
            {
              "Name": "render",
              "Environment": [{ "Name": "JOB_ID", "Value.$": "$.job_id" }]
            }
          ]
        }
      },
      "ResultPath": "$.render_result",
      "Retry": [{ "ErrorEquals": ["States.ALL"], "IntervalSeconds": 5, "MaxAttempts": 1 }],
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "RenderOnDemand" }],
      "Next": "ReleaseSlotSuccess"
    },
    "RenderOnDemand": {
      "Type": "Task",
      "Resource": "arn:aws:states:::ecs:runTask.sync",
      "Parameters": {
        "Cluster": "${ecs_cluster_arn}",
        "TaskDefinition": "${render_task_definition_arn}",
        "CapacityProviderStrategy": [{ "CapacityProvider": "FARGATE", "Weight": 1 }],
        "NetworkConfiguration": {
          "AwsvpcConfiguration": {
            "Subnets": ${public_subnet_ids_json},
            "SecurityGroups": ["${render_task_sg_id}"],
            "AssignPublicIp": "ENABLED"
          }
        },
        "Overrides": {
          "ContainerOverrides": [
            {
              "Name": "render",
              "Environment": [{ "Name": "JOB_ID", "Value.$": "$.job_id" }]
            }
          ]
        }
      },
      "ResultPath": "$.render_result",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "ReleaseSlotFailure" }],
      "Next": "ReleaseSlotSuccess"
    },
    "ReleaseSlotSuccess": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${semaphore_release_arn}",
        "Payload.$": "$"
      },
      "ResultSelector": { "job_id.$": "$.Payload.job_id" },
      "ResultPath": "$",
      "Next": "Finish",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "ReleaseSlotFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${semaphore_release_arn}",
        "Payload.$": "$"
      },
      "ResultSelector": { "job_id.$": "$.Payload.job_id" },
      "ResultPath": "$.release_result",
      "Next": "JobFailed",
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "Finish": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${finish_arn}",
        "Payload.$": "$"
      },
      "ResultSelector": { "job_id.$": "$.Payload.job_id", "url.$": "$.Payload.url" },
      "ResultPath": "$",
      "End": true,
      "Catch": [{ "ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "JobFailed" }]
    },
    "JobFailed": {
      "Type": "Task",
      "Resource": "arn:aws:states:::dynamodb:updateItem",
      "Parameters": {
        "TableName": "${jobs_table_name}",
        "Key": { "pk": { "S.$": "States.Format('JOB#{}', $.job_id)" } },
        "UpdateExpression": "SET #status = :failed, #error = :error",
        "ExpressionAttributeNames": { "#status": "status", "#error": "error" },
        "ExpressionAttributeValues": {
          ":failed": { "S": "FAILED" },
          ":error": { "S.$": "States.Format('{}: {}', $.error.Error, $.error.Cause)" }
        }
      },
      "ResultPath": null,
      "Next": "PublishJobFailedEvent"
    },
    "PublishJobFailedEvent": {
      "Type": "Task",
      "Resource": "arn:aws:states:::events:putEvents",
      "Parameters": {
        "Entries": [
          {
            "Source": "montage.pipeline",
            "DetailType": "job.failed",
            "Detail": {
              "job_id.$": "$.job_id",
              "status": "FAILED",
              "error.$": "States.Format('{}: {}', $.error.Error, $.error.Cause)"
            }
          }
        ]
      },
      "ResultPath": null,
      "Next": "SendToDlq"
    },
    "SendToDlq": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sqs:sendMessage",
      "Parameters": {
        "QueueUrl": "${dlq_url}",
        "MessageBody.$": "States.JsonToString($)"
      },
      "ResultPath": null,
      "Next": "Failed"
    },
    "Failed": {
      "Type": "Fail"
    }
  }
}
