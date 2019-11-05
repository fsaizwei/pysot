# Copyright (c) SenseTime. All Rights Reserved.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
import torch
import torch.nn as nn
import torch.nn.functional as F

from pysot.core.config import cfg
from pysot.models.loss import select_cross_entropy_loss, weight_l1_loss
from pysot.models.backbone import get_backbone
from pysot.models.head import get_rpn_head, get_mask_head, get_refine_head
from pysot.models.neck import get_neck


from typing import List

class ModelBuilder(nn.Module):
    #xf: torch.jit.Final[Tensor]
    __annotations__ = {'xf': List[torch.Tensor]}

    def __init__(self):
        super(ModelBuilder, self).__init__()

        # build backbone
        self.backbone = get_backbone(cfg.BACKBONE.TYPE,
                                     **cfg.BACKBONE.KWARGS)

        # build adjust layer
        if cfg.ADJUST.ADJUST:
            self.neck = get_neck(cfg.ADJUST.TYPE,
                                 **cfg.ADJUST.KWARGS)

        # build rpn head
        self.rpn_head = get_rpn_head(cfg.RPN.TYPE,
                                     **cfg.RPN.KWARGS)

        # build mask head
        if cfg.MASK.MASK:
            self.mask_head = get_mask_head(cfg.MASK.TYPE,
                                           **cfg.MASK.KWARGS)

            if cfg.REFINE.REFINE:
                self.refine_head = get_refine_head(cfg.REFINE.TYPE)

    def template(self, z):
        zf = self.backbone(z)
        if cfg.MASK.MASK:
            zf = zf[-1]
        if cfg.ADJUST.ADJUST:
            zf = self.neck(zf)
        self.zf = zf

    @torch.jit.script_method
    def track(self, x):
        xf_local = self.backbone(x)
        if cfg.MASK.MASK:
            self.xf = xf_local[:-1]
            xf = xf_local[-1]
        if cfg.ADJUST.ADJUST:
            xf_n = self.neck(xf)
        cls, loc = self.rpn_head(self.zf, xf_n)
        if cfg.MASK.MASK:
            mask, self.mask_corr_feature = self.mask_head(self.zf, xf_n)
        return {
                'cls': cls,
                'loc': loc,
                'mask': mask if cfg.MASK.MASK else None
               }

    def mask_refine(self, pos):
        return self.refine_head(self.xf, self.mask_corr_feature, pos)

    def log_softmax(self, cls):
        b, a2, h, w = cls.size()
        cls = cls.view(b, 2, a2//2, h, w)
        cls = cls.permute(0, 2, 3, 4, 1).contiguous()
        cls = F.log_softmax(cls, dim=4)
        return cls

    def forward(self, data):
        """ only used in training
        """
        template = data['template'].cuda()
        search = data['search'].cuda()
        label_cls = data['label_cls'].cuda()
        label_loc = data['label_loc'].cuda()
        label_loc_weight = data['label_loc_weight'].cuda()

        # get feature
        zf = self.backbone(template)
        xf = self.backbone(search)
        if cfg.MASK.MASK:
            zf = zf[-1]
            self.xf_refine = xf[:-1]
            xf = xf[-1]
        if cfg.ADJUST.ADJUST:
            zf = self.neck(zf)
            xf = self.neck(xf)
        cls, loc = self.rpn_head(zf, xf)

        # get loss
        cls = self.log_softmax(cls)
        cls_loss = select_cross_entropy_loss(cls, label_cls)
        loc_loss = weight_l1_loss(loc, label_loc, label_loc_weight)

        outputs = {}
        outputs['total_loss'] = cfg.TRAIN.CLS_WEIGHT * cls_loss + \
            cfg.TRAIN.LOC_WEIGHT * loc_loss
        outputs['cls_loss'] = cls_loss
        outputs['loc_loss'] = loc_loss

        if cfg.MASK.MASK:
            # TODO
            mask, self.mask_corr_feature = self.mask_head(zf, xf)
            mask_loss = None
            outputs['total_loss'] += cfg.TRAIN.MASK_WEIGHT * mask_loss
            outputs['mask_loss'] = mask_loss
        return outputs

    def save_script(self, save_path):
        back_bone_script = torch.jit.script(self.backbone)
        back_bone_script.save(os.path.join(save_path, "back_bone.pt"))
        print("Save backbone as {}".format(os.path.join(save_path, "back_bone.pt")))

        if self.neck:
            neck_script = torch.jit.script(self.neck)
            neck_script.save(os.path.join(save_path, "neck.pt"))
            print("Save neckscript as {}".format(os.path.join(save_path, "neck.pt")))

        if self.rpn_head:
            rpn_head_script = torch.jit.script(self.rpn_head)
            rpn_head_script.save(os.path.join(save_path, "rpn_head.pt"))
            print("Save rpn_head_script as {}".format(os.path.join(save_path, "rpn_head.pt")))

        if self.mask_head:
            mask_head_script = torch.jit.script(self.mask_head)
            mask_head_script.save(os.path.join(save_path, "mask_head.pt"))
            print("Save mask_head_script as {}".format(os.path.join(save_path, "mask_head.pt")))

            if self.refine_head:
                refine_head_script = torch.jit.script(self.refine_head)
                refine_head_script.save(os.path.join(save_path, "refine_head.pt"))
                print("Save refine_head_script as {}".format(os.path.join(save_path, "refine_head.pt")))