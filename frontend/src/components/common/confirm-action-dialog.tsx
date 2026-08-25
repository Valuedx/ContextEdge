"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type ConfirmActionDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  confirmationText?: string;
  confirmationLabel?: string;
  isPending?: boolean;
  onConfirm: () => void;
};

export function ConfirmActionDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  confirmationText,
  confirmationLabel,
  isPending = false,
  onConfirm,
}: ConfirmActionDialogProps) {
  const [typedValue, setTypedValue] = useState("");
  const needsTypedConfirmation = Boolean(confirmationText);
  const canConfirm = !needsTypedConfirmation || typedValue === confirmationText;

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) setTypedValue("");
    onOpenChange(nextOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {needsTypedConfirmation && (
          <div className="space-y-2">
            <label htmlFor="confirm-action-text" className="text-sm font-medium">
              {confirmationLabel ?? `Type ${confirmationText} to confirm`}
            </label>
            <Input
              id="confirm-action-text"
              value={typedValue}
              onChange={(event) => setTypedValue(event.target.value)}
              autoComplete="off"
            />
          </div>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={onConfirm}
            disabled={isPending || !canConfirm}
          >
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
