# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""generative-media-digest — the weekly generative-media digest pipeline.

A self-contained twin of the daily ``app`` package, focused on generative media
(image / video generation, image & video editing, speech and music generation).
The production path is the headless runner (``python -m app_media.runner``) built
on ``config``, ``pipeline``, ``render``. It writes under ``output_media/`` and is
published to a separate ``media-digest/`` site section so it never collides with
the daily AI-agent digest.
"""
