#include "system/loggerd/encoder/ffmpeg_encoder.h"

#include <fcntl.h>
#include <unistd.h>

#include <cassert>
#include <cstdio>
#include <cstdlib>

#define __STDC_CONSTANT_MACROS

#include "libyuv.h"

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/imgutils.h>
}

#include "common/swaglog.h"
#include "common/util.h"

const int env_debug_encoder = (getenv("DEBUG_ENCODER") != NULL) ? atoi(getenv("DEBUG_ENCODER")) : 0;

FfmpegEncoder::FfmpegEncoder(const EncoderInfo &encoder_info, int in_width, int in_height)
    : VideoEncoder(encoder_info, in_width, in_height) {
  frame = av_frame_alloc();
  assert(frame);
  frame->format = AV_PIX_FMT_YUV420P;
  frame->width = out_width;
  frame->height = out_height;
  frame->linesize[0] = out_width;
  frame->linesize[1] = out_width/2;
  frame->linesize[2] = out_width/2;

  convert_buf.resize(in_width * in_height * 3 / 2);

  if (in_width != out_width || in_height != out_height) {
    downscale_buf.resize(out_width * out_height * 3 / 2);
  }
}

FfmpegEncoder::~FfmpegEncoder() {
  encoder_close();
  av_frame_free(&frame);
}

void FfmpegEncoder::encoder_open() {
  auto encode_type = encoder_info.get_settings(in_width).encode_type;
  auto codec_id = encode_type == cereal::EncodeIndex::Type::QCAMERA_H264
                      ? AV_CODEC_ID_H264
                      : AV_CODEC_ID_FFVHUFF;

  // Try RK3588 hardware encoder first (h264_rkmpp), fall back to software
  const AVCodec *codec = nullptr;
  if (codec_id == AV_CODEC_ID_H264) {
    codec = avcodec_find_encoder_by_name("h264_rkmpp");
    if (codec) {
      LOGW("Using RK3588 hardware encoder (h264_rkmpp)");
    }
  }
  if (!codec) {
    codec = avcodec_find_encoder(codec_id);
    if (codec_id == AV_CODEC_ID_H264) {
      LOGW("Using software encoder (libx264)");
    }
  }
  assert(codec);

  this->codec_ctx = avcodec_alloc_context3(codec);
  assert(this->codec_ctx);
  this->codec_ctx->width = frame->width;
  this->codec_ctx->height = frame->height;
  this->codec_ctx->time_base = (AVRational){ 1, encoder_info.fps };
  this->codec_ctx->gop_size = encoder_info.fps;  // keyframe every second
  this->codec_ctx->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;

  hw_encoder = (strcmp(codec->name, "h264_rkmpp") == 0);

  AVDictionary *opts = NULL;
  if (hw_encoder) {
    // RKMPP hardware encoder accepts NV12 directly — no I420 conversion needed
    this->codec_ctx->pix_fmt = AV_PIX_FMT_NV12;
    frame->format = AV_PIX_FMT_NV12;
  } else {
    this->codec_ctx->pix_fmt = AV_PIX_FMT_YUV420P;
    frame->format = AV_PIX_FMT_YUV420P;
    if (codec_id == AV_CODEC_ID_H264) {
      av_dict_set(&opts, "preset", "ultrafast", 0);
      av_dict_set(&opts, "tune", "zerolatency", 0);
    }
  }

  int err = avcodec_open2(this->codec_ctx, codec, &opts);
  av_dict_free(&opts);
  if (err < 0 && hw_encoder) {
    // Hardware encoder failed — fall back to software
    LOGW("h264_rkmpp failed (%d), falling back to libx264", err);
    avcodec_free_context(&codec_ctx);
    hw_encoder = false;

    codec = avcodec_find_encoder(codec_id);
    this->codec_ctx = avcodec_alloc_context3(codec);
    this->codec_ctx->width = frame->width;
    this->codec_ctx->height = frame->height;
    this->codec_ctx->pix_fmt = AV_PIX_FMT_YUV420P;
    this->codec_ctx->time_base = (AVRational){ 1, encoder_info.fps };
    this->codec_ctx->gop_size = encoder_info.fps;
    this->codec_ctx->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
    frame->format = AV_PIX_FMT_YUV420P;

    opts = NULL;
    av_dict_set(&opts, "preset", "ultrafast", 0);
    av_dict_set(&opts, "tune", "zerolatency", 0);
    err = avcodec_open2(this->codec_ctx, codec, &opts);
    av_dict_free(&opts);
  }
  assert(err >= 0);

  is_open = true;
  segment_num++;
  counter = 0;
}

void FfmpegEncoder::encoder_close() {
  if (!is_open) return;

  avcodec_free_context(&codec_ctx);
  is_open = false;
}

int FfmpegEncoder::encode_frame(VisionBuf* buf, VisionIpcBufExtra *extra) {
  assert(buf->width == this->in_width);
  assert(buf->height == this->in_height);

  if (hw_encoder) {
    // Hardware encoder: feed NV12 directly from VisionBuf (zero conversion)
    if (downscale_buf.size() > 0) {
      // Need to downscale — convert NV12→I420, scale, then I420→NV12
      uint8_t *cy = convert_buf.data();
      uint8_t *cu = cy + in_width * in_height;
      uint8_t *cv = cu + (in_width / 2) * (in_height / 2);
      libyuv::NV12ToI420(buf->y, buf->stride,
                         buf->uv, buf->stride,
                         cy, in_width,
                         cu, in_width/2,
                         cv, in_width/2,
                         in_width, in_height);

      uint8_t *out_y = downscale_buf.data();
      uint8_t *out_u = out_y + frame->width * frame->height;
      uint8_t *out_v = out_u + (frame->width / 2) * (frame->height / 2);
      libyuv::I420Scale(cy, in_width,
                        cu, in_width/2,
                        cv, in_width/2,
                        in_width, in_height,
                        out_y, frame->width,
                        out_u, frame->width/2,
                        out_v, frame->width/2,
                        frame->width, frame->height,
                        libyuv::kFilterNone);
      // Convert scaled I420 back to NV12 for hardware encoder
      uint8_t *nv12_y = convert_buf.data();
      uint8_t *nv12_uv = nv12_y + frame->width * frame->height;
      libyuv::I420ToNV12(out_y, frame->width,
                         out_u, frame->width/2,
                         out_v, frame->width/2,
                         nv12_y, frame->width,
                         nv12_uv, frame->width,
                         frame->width, frame->height);
      frame->data[0] = nv12_y;
      frame->data[1] = nv12_uv;
      frame->linesize[0] = frame->width;
      frame->linesize[1] = frame->width;
    } else {
      // No downscale — pass VisionBuf NV12 directly (zero-copy)
      frame->data[0] = buf->y;
      frame->data[1] = buf->uv;
      frame->linesize[0] = buf->stride;
      frame->linesize[1] = buf->stride;
    }
  } else {
    // Software encoder: convert NV12 → I420
    uint8_t *cy = convert_buf.data();
    uint8_t *cu = cy + in_width * in_height;
    uint8_t *cv = cu + (in_width / 2) * (in_height / 2);
    libyuv::NV12ToI420(buf->y, buf->stride,
                       buf->uv, buf->stride,
                       cy, in_width,
                       cu, in_width/2,
                       cv, in_width/2,
                       in_width, in_height);

    if (downscale_buf.size() > 0) {
      uint8_t *out_y = downscale_buf.data();
      uint8_t *out_u = out_y + frame->width * frame->height;
      uint8_t *out_v = out_u + (frame->width / 2) * (frame->height / 2);
      libyuv::I420Scale(cy, in_width,
                        cu, in_width/2,
                        cv, in_width/2,
                        in_width, in_height,
                        out_y, frame->width,
                        out_u, frame->width/2,
                        out_v, frame->width/2,
                        frame->width, frame->height,
                        libyuv::kFilterNone);
      frame->data[0] = out_y;
      frame->data[1] = out_u;
      frame->data[2] = out_v;
    } else {
      frame->data[0] = cy;
      frame->data[1] = cu;
      frame->data[2] = cv;
    }
  }
  frame->pts = counter*50*1000; // 50ms per frame

  int ret = counter;

  int err = avcodec_send_frame(this->codec_ctx, frame);
  if (err < 0) {
    LOGE("avcodec_send_frame error %d", err);
    ret = -1;
  }

  AVPacket pkt = {};
  pkt.data = NULL;
  pkt.size = 0;
  while (ret >= 0) {
    err = avcodec_receive_packet(this->codec_ctx, &pkt);
    if (err == AVERROR_EOF) {
      break;
    } else if (err == AVERROR(EAGAIN)) {
      // Encoder might need a few frames on startup to get started. Keep going
      ret = 0;
      break;
    } else if (err < 0) {
      LOGE("avcodec_receive_packet error %d", err);
      ret = -1;
      break;
    }

    if (env_debug_encoder) {
      printf("%20s got %8d bytes flags %8x idx %4d id %8d\n", encoder_info.publish_name, pkt.size, pkt.flags, counter, extra->frame_id);
    }

    // Pass codec extradata (SPS/PPS) as header on keyframes for WebRTC decoder init
    auto header = (pkt.flags & AV_PKT_FLAG_KEY) && codec_ctx->extradata_size > 0
      ? kj::arrayPtr<capnp::byte>(codec_ctx->extradata, codec_ctx->extradata_size)
      : kj::arrayPtr<capnp::byte>(pkt.data, (size_t)0);
    publisher_publish(segment_num, counter, *extra,
      (pkt.flags & AV_PKT_FLAG_KEY) ? V4L2_BUF_FLAG_KEYFRAME : 0,
      header,
      kj::arrayPtr<capnp::byte>(pkt.data, pkt.size));

    counter++;
  }
  av_packet_unref(&pkt);
  return ret;
}
