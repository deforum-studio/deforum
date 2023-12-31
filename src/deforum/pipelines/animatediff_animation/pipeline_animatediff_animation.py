import json
import os
import sys
from typing import Callable, Optional

import numpy as np
import pandas as pd
import torch

from ..deforum_pipeline import DeforumBase
from ...pipeline_utils import DeforumGenerationObject, extract_values
from ...utils.constants import comfy_path
from ...utils.video_save_util import save_as_h264


def parse_weight_string(weight_string, max_frames):
    # If empty string, return list with all 1.0 values
    if not weight_string:
        return [1.0] * max_frames

    # Parse the string
    parts = weight_string.split(',')
    keyframe_dict = {}
    for part in parts:
        key, value = part.split(':')
        key = int(key.strip())
        value = float(value.strip('() '))
        keyframe_dict[key] = value

    # Create a series with NaN values
    series = pd.Series([np.nan] * max_frames)

    # Set the parsed values
    for key, value in keyframe_dict.items():
        series[key] = value

    # Linearly interpolate missing values and fill any remaining NaNs with the backfill method
    series.interpolate(method='linear', inplace=True)
    series.fillna(method='bfill', inplace=True)
    series.fillna(method='ffill', inplace=True)

    return series.tolist()

class DeforumAnimateDiffPipeline(DeforumBase):

    def __init__(self, generator: Callable, logger: Optional[Callable] = None):

        super().__init__()
        self.generator = generator
        self.logger = logger
        self.animatediff_path = os.path.join(comfy_path, "custom_nodes", "ComfyUI-AnimateDiff-Evolved")
        sys.path.append(self.animatediff_path)
        from animatediff import sampling

        import comfy.sample as comfy_sample
        # override comfy_sample.sample with animatediff-support version
        comfy_sample.sample = sampling.animatediff_sample_factory(comfy_sample.sample)

        def get_motion_model_path(model_name: str):
            return os.path.join(self.animatediff_path, 'models', model_name)
        import animatediff.motion_module
        animatediff.motion_module.get_motion_model_path = get_motion_model_path

        from deforum.utils.file_dl_util import download_file_to

        url = "https://huggingface.co/hotshotco/Hotshot-XL/resolve/main/hsxl_temporal_layers.f16.safetensors"
        destination = os.path.join(self.animatediff_path, 'models')
        self.module_filename = url.split('/')[-1]

        hotshotxl_path = os.path.join(destination, self.module_filename)

        if not os.path.isfile(hotshotxl_path):
            hotshotxl_path = download_file_to(url=url, destination_dir=destination, filename=self.module_filename)

        self.prep_fns = []
        self.shoot_fns = []
        self.post_fns = []


    def __call__(self, settings_file:Optional[str] = None, *args, **kwargs):
        """
        AnimateDiff / HotshotCo-XL Sampler function with sliding context

        We must do some imports on the call level since they are not part of the base repo and are downloaded /
        maintained separately by (generators.comfy_utils.ensure_comfy)
        Args:
            settings_file:
            *args:
            **kwargs:

        Returns:

        """


        if settings_file:
            self.gen = DeforumGenerationObject.from_settings_file(settings_file)
        else:
            self.gen = DeforumGenerationObject()

        animate_diff_defaults = {
            "context_length": {
                "label": "Context Length",
                "type": "number",
                "minimum": 0,
                "maximum": 32,
                "step": 1,
                "value": 8,
                "visible": True
            },
            "context_stride": {
                "label": "Context Stride",
                "type": "number",
                "minimum": 1,
                "maximum": 32,
                "step": 1,
                "value": 1,
                "visible": True
            },
            "context_overlap": {
                "label": "Context Overlap",
                "type": "number",
                "minimum": 0,
                "maximum": 32,
                "step": 1,
                "value": 4,
                "visible": True
            },
            "max_frames": {
                "label": "Max Frames",
                "type": "number",
                "minimum": 1,
                "maximum": 4096,
                "step": 1,
                "value": 16,
                "visible": True
            },
            "closed_loop": {
                "label": "Closed Loop",
                "type": "checkbox",
                "value": True,
                "info": "",
                "visible": True
            },

        }
        prompts = {
            0: "A dog walking in the park",
        }

        #Create default values needed for animatediff / Hotshotco-XL generation
        self.gen.prompts = json.loads(json.dumps(prompts))

        defaults = extract_values(animate_diff_defaults)
        #Update generation params from any kwarg passed to the call
        self.gen.update_from_kwargs(prompts=prompts, **defaults)
        #Create 'sliding context' for animatediff / Hotshotco-XL
        #TODO Move functions in the the loops as and if they make sense
        _ = self.run_animatediff()

    def create_context(self):
        from custom_nodes.ComfyUI_FizzNodes import PromptScheduleEncodeSDXL
        from animatediff.context import UniformContextOptions
        self.context_options = UniformContextOptions(
            context_length=self.gen.context_length, # ("INT", {"default": 16, "min": 1, "max": 32})
            context_stride=self.gen.context_stride, # ("INT", {"default": 1, "min": 1, "max": 32})
            context_overlap=self.gen.context_overlap, # ("INT", {"default": 4, "min": 0, "max": 32})
            context_schedule="uniform", #(ContextSchedules.CONTEXT_SCHEDULE_LIST,)
            closed_loop=self.gen.closed_loop, #("BOOLEAN", {"default": False},)
            )
        prompt_scheduler = PromptScheduleEncodeSDXL()
        # "crop_w": ("INT", {"default": 0, "min": 0, "max": MAX_RESOLUTION}),
        # "crop_h": ("INT", {"default": 0, "min": 0, "max": MAX_RESOLUTION}),
        # "target_width": ("INT", {"default": 1024.0, "min": 0, "max": MAX_RESOLUTION}),
        # "target_height": ("INT", {"default": 1024.0, "min": 0, "max": MAX_RESOLUTION}),
        # "text_g": ("STRING", {"multiline": True, "default": "CLIP_G"}), "clip": ("CLIP",),
        # "text_l": ("STRING", {"multiline": True, "default": "CLIP_L"}), "clip": ("CLIP",),
        # "max_frames": ("INT", {"default": 120.0, "min": 1.0, "max": 9999.0, "step": 1.0}),
        # "print_output": ("BOOLEAN", {"default": False}), },
        # "optional": {"pre_text_G": ("STRING", {"multiline": False, }),  # "forceInput": True}),
        # "app_text_G": ("STRING", {"multiline": False, }),  # "forceInput": True}),
        # "pre_text_L": ("STRING", {"multiline": False, }),  # "forceInput": True}),
        # "app_text_L": ("STRING", {"multiline": False, }),  # "forceInput": True}),
        # "pw_a": ("FLOAT", {"default": 0.0, "min": -9999.0, "max": 9999.0, "step": 0.1, }),  # "forceInput": True }),
        # "pw_b": ("FLOAT", {"default": 0.0, "min": -9999.0, "max": 9999.0, "step": 0.1, }),  # "forceInput": True }),
        # "pw_c": ("FLOAT", {"default": 0.0, "min": -9999.0, "max": 9999.0, "step": 0.1, }),  # "forceInput": True }),
        # "pw_d": ("FLOAT", {"default": 0.0, "min": -9999.0, "max": 9999.0, "step": 0.1, }),  # "forceInput": True }),
        self.conds = prompt_scheduler.animate(clip=self.generator.clip,
                                              width=self.gen.W,
                                              height=self.gen.H,
                                              crop_w=0,
                                              crop_h=0,
                                              target_width=self.gen.W,
                                              target_height=self.gen.H,
                                              text_g=json.dumps(self.gen.prompts).strip("{}"),
                                              text_l=json.dumps(self.gen.prompts).strip("{}"),
                                              max_frames=self.gen.max_frames,
                                              current_frame=0,
                                              pre_text_L="Abstract art",
                                              pre_text_G="",
                                              app_text_L="",
                                              app_text_G="",
                                              pw_a=[1.0 for _ in range(self.gen.max_frames)],#parse_weight_string("", self.gen.max_frames),
                                              pw_b=[1.0 for _ in range(self.gen.max_frames)],#parse_weight_string("", self.gen.max_frames),
                                              pw_c=[1.0 for _ in range(self.gen.max_frames)],#parse_weight_string("", self.gen.max_frames),
                                              pw_d=[1.0 for _ in range(self.gen.max_frames)],#parse_weight_string("", self.gen.max_frames),
                                              )[0]

        # self.conds = prompt_scheduler.animate(text=json.dumps(self.gen.prompts).strip("{}"),
        #                                      max_frames=self.gen.max_frames,
        #                                      print_output=True,
        #                                      clip=self.generator.clip,
        #                                      pw_a=parse_weight_string("", self.gen.max_frames),
        #                                      pw_b=parse_weight_string("", self.gen.max_frames),
        #                                      pw_c=parse_weight_string("", self.gen.max_frames),
        #                                      pw_d=parse_weight_string("", self.gen.max_frames),
        #                                      )
        #

    @torch.inference_mode()
    def run_animatediff(self):
        from animatediff.model_utils import BetaSchedules
        from animatediff.motion_module import load_motion_module, inject_params_into_model, InjectionParams
        from animatediff.context import UniformContextOptions



        self.create_context()

        #TODO load motion module
        motion_lora = None

        mm = load_motion_module(self.module_filename, motion_lora, model=self.generator.model)
        # set injection params
        injection_params = InjectionParams(
                video_length=None,
                unlimited_area_hack=False,
                apply_mm_groupnorm_hack=True,
                beta_schedule=BetaSchedules.LINEAR, # beta_schedule
                injector=mm.injector_version,
                model_name=self.module_filename,
        )
        if self.context_options:
            # set context settings TODO: make this dynamic for future purposes
            if type(self.context_options) == UniformContextOptions:
                injection_params.set_context(
                        context_length=self.context_options.context_length,
                        context_stride=self.context_options.context_stride,
                        context_overlap=self.context_options.context_overlap,
                        context_schedule=self.context_options.context_schedule,
                        closed_loop=self.context_options.closed_loop
                )
        if motion_lora:
            injection_params.set_loras(motion_lora)
        # inject for use in sampling code
        self.generator.model = inject_params_into_model(self.generator.model, injection_params)


        height = self.gen.H
        width = self.gen.W
        batch_size = self.gen.max_frames

        prompt = "Mona Lisa walking in new york"
        n_prompt = ""

        latent = torch.randn([self.gen.max_frames, 4, height // 8, width // 8]).to("cuda")

        images = self.generator( pooled_prompts=self.conds,
                                 latent=latent,
                                 strength=1.0,
                                 steps=25)

        self.gen.frame_interpolation_engine = "FILM"
        self.gen.frame_interpolation_x_amount = 2

        self.images = [np.array(image) for image in images]

        from deforum.pipelines.deforum_animation.animation_helpers import film_interpolate_cls

        film_interpolate_cls(self)

        save_as_h264(self.images, f"output/video/{self.gen.batch_name}.mp4")

        self.gen.video_path = f"output/video/{self.gen.batch_name}.mp4"

        return self.gen
